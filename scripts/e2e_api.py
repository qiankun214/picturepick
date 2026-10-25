"""PicturePick 后端 API 端到端验收脚本。

用法（后端已在跑）：
    python scripts/e2e_api.py --base-url http://127.0.0.1:8123 --photos-dir samples/photos

它会完整走一遍：健康检查 → 目录浏览 → 创建会话 → 分页/图片/缩略图 → 浏览 & pick 计数 →
连续多轮筛选 → 结束生成结果 JSON → 下载/导出 → 错误码校验，
最后追加「AI 识鸟」链路（配置/状态/连通性 → 提交任务与队列 → 进度/参考图 → 采纳与 EXIF 写回
→ 忽略/失败分支 → 结果 JSON / restore 保留 bird）。

AI 识鸟部分需要后端以模拟模式运行（不访问网络）：
    $env:PICTUREPICK_BIRD_MOCK='1'; $env:PICTUREPICK_BIRD_MOCK_DELAY='0.25'
    $env:PICTUREPICK_CONFIG_DIR='E:\picturePick\.scratch\e2e-config'
    python -m picturepick serve --port 8126 --no-open
识鸟会修改照片 EXIF，因此脚本把照片目录复制到 ``--out-dir/bird-photos`` 后再采纳，
不会改动原始照片目录；配置在章节结束前恢复。
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def expect_error(resp: httpx.Response, status: int, code: str) -> bool:
    try:
        body = resp.json()
    except Exception:
        return False
    return resp.status_code == status and body.get("detail", {}).get("code") == code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8123")
    parser.add_argument("--photos-dir", required=True)
    parser.add_argument("--out-dir", default=None, help="导出 JSON 的目录，默认工作区 .tmp/e2e")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    photos_dir = str(Path(args.photos_dir).resolve())
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent.parent / ".tmp" / "e2e"
    out_dir.mkdir(parents=True, exist_ok=True)

    expected = sorted(
        p for p in Path(photos_dir).rglob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"}
    )
    browser_photos = []
    for p in expected:
        stat = p.stat()
        browser_photos.append(
            {
                "relativePath": p.relative_to(Path(photos_dir)).as_posix(),
                "sizeBytes": stat.st_size,
                "lastModified": int(stat.st_mtime * 1000),
            }
        )
    print(f"照片目录: {photos_dir}  (期望 {len(expected)} 张 jpg/jpeg)")

    with httpx.Client(base_url=base, timeout=30.0) as c:
        print("\n[1] 健康检查")
        health = c.get("/api/health")
        check("GET /api/health 200", health.status_code == 200)
        check("version 为 1.0.0", health.json().get("version") == "1.0.0", str(health.json()))

        print("\n[2] 目录浏览")
        roots = c.get("/api/fs/roots")
        check("/api/fs/roots 200 且有 roots", roots.status_code == 200 and bool(roots.json().get("roots")))
        parent = str(Path(photos_dir).parent)
        listing = c.get("/api/fs/list", params={"path": parent})
        entries = listing.json().get("entries", []) if listing.status_code == 200 else []
        names = {e["name"] for e in entries}
        check("/api/fs/list 返回父目录且包含照片目录", Path(photos_dir).name in names, str(entries)[:300])
        check("/api/fs/list 只返回目录", all(e.get("isDir") for e in entries))
        check("/api/fs/list 非法路径 400 INVALID_PATH", expect_error(c.get("/api/fs/list", params={"path": "Z:\\no\\such\\dir"}), 400, "INVALID_PATH"))

        print("\n[3] 创建会话（并验证 0 张 pick 时不能进入下一轮）")
        created = c.post("/api/sessions", json={"directory": photos_dir, "recursive": True})
        check("POST /api/sessions 201", created.status_code == 201, created.text[:300])
        session = created.json()
        sid = session["id"]
        check("libraryTotal 等于期望张数", session["libraryTotal"] == len(expected), f"{session['libraryTotal']} vs {len(expected)}")
        check("初始 roundIndex=1, viewedCount=0, pickCount=0",
              (session["roundIndex"], session["viewedCount"], session["pickCount"]) == (1, 0, 0),
              json.dumps({k: session[k] for k in ("roundIndex", "viewedCount", "pickCount")}))
        check("/api/sessions/current 指向该会话", c.get("/api/sessions/current").json()["session"]["id"] == sid)
        check("非法目录 400 INVALID_PATH", expect_error(c.post("/api/sessions", json={"directory": "Z:\\no\\such"}), 400, "INVALID_PATH"))
        empty_dir = Path(tempfile.mkdtemp(dir=str(out_dir)))
        check("无照片目录 400 NO_PHOTOS", expect_error(c.post("/api/sessions", json={"directory": str(empty_dir)}), 400, "NO_PHOTOS"))

        print("\n[4] 本轮照片分页")
        page = c.get(f"/api/sessions/{sid}/rounds/1/photos", params={"offset": 0, "limit": 1000})
        check("GET 本轮照片 200", page.status_code == 200, page.text[:300])
        pdata = page.json()
        photos = pdata["photos"]
        check("总数与库一致", pdata["total"] == len(expected) and len(photos) == len(expected), f"{pdata['total']} / {len(photos)}")
        check("全部是 jpg/jpeg", all(p["name"].lower().endswith((".jpg", ".jpeg")) for p in photos))
        check("字段完整", all({"id", "name", "relativePath", "pickCount", "picked", "viewed"} <= set(p) for p in photos))
        page2 = c.get(f"/api/sessions/{sid}/rounds/1/photos", params={"offset": 1, "limit": 2}).json()
        check("offset/limit 生效", len(page2["photos"]) == 2 and page2["photos"][0]["id"] == photos[1]["id"])

        print("\n[5] 图片与缩略图")
        pid = photos[0]["id"]
        full = c.get(f"/api/sessions/{sid}/photos/{pid}/image")
        check("原图 200 image/jpeg", full.status_code == 200 and full.headers["content-type"].startswith("image/jpeg"))
        check("原图是 JPEG 数据", full.content[:2] == b"\xff\xd8", str(full.content[:8]))
        thumb = c.get(f"/api/sessions/{sid}/photos/{pid}/image", params={"variant": "thumb"})
        check("缩略图 200", thumb.status_code == 200)
        try:
            from PIL import Image
            full_im = Image.open(io.BytesIO(full.content))
            thumb_im = Image.open(io.BytesIO(thumb.content))
            check("缩略图最长边 <= 512", max(thumb_im.size) <= 512, str(thumb_im.size))
            check("缩略图小于原图", len(thumb.content) < len(full.content))
        except ImportError:
            pass
        check("不存在照片 404 PHOTO_NOT_FOUND", expect_error(c.get(f"/api/sessions/{sid}/photos/deadbeefdeadbeef/image"), 404, "PHOTO_NOT_FOUND"))
        check("未知会话 404 SESSION_NOT_FOUND", expect_error(c.get("/api/sessions/nosuchsession"), 404, "SESSION_NOT_FOUND"))

        print("\n[6] 浏览与 pick 计数")
        for p in photos[:3]:
            r = c.post(f"/api/sessions/{sid}/photos/{p['id']}/viewed")
            check(f"viewed {p['name']}", r.status_code == 200 and r.json()["viewedCount"] == photos[:3].index(p) + 1, r.text[:200])
        r = c.post(f"/api/sessions/{sid}/photos/{photos[0]['id']}/viewed")
        check("viewed 幂等", r.status_code == 200 and r.json()["viewedCount"] == 3, r.text[:200])

        for p in photos[:3]:
            r = c.put(f"/api/sessions/{sid}/photos/{p['id']}/pick")
            check(f"pick {p['name']}", r.status_code == 200 and r.json()["pickCount"] == photos[:3].index(p) + 1, r.text[:200])
        state = c.get(f"/api/sessions/{sid}").json()
        check("pickCount=3", state["pickCount"] == 3, json.dumps(state)[:300])
        check("photo.pickCount=1", c.get(f"/api/sessions/{sid}/rounds/1/photos", params={"limit": 5}).json()["photos"][0]["pickCount"] == 1)
        again = c.put(f"/api/sessions/{sid}/photos/{photos[0]['id']}/pick")
        check("重复 pick 幂等", again.json()["pickCount"] == 3, again.text[:200])
        unpick = c.delete(f"/api/sessions/{sid}/photos/{photos[2]['id']}/pick")
        check("取消 pick 后 pickCount=2", unpick.json()["pickCount"] == 2, unpick.text[:200])

        print("\n[7] 多轮筛选")
        # 第 6 步之后本轮已 pick 2 张，应允许进入下一轮
        need2 = c.post(f"/api/sessions/{sid}/rounds/next")
        check("pick 2 张后 next 200", need2.status_code == 200, need2.text[:200])
        if need2.status_code == 200:
            st2 = need2.json()
            check("进入第 2 轮", st2["roundIndex"] == 2 and st2["roundCount"] == 2 and st2["roundPhotoCount"] == 2, json.dumps(st2)[:300])
            r2 = c.get(f"/api/sessions/{sid}/rounds/2/photos").json()
            check("第 2 轮照片为已 pick 的 2 张", {p["id"] for p in r2["photos"]} == {photos[0]["id"], photos[1]["id"]})
            check("第 2 轮初始 picked 均为 False", all(not p["picked"] for p in r2["photos"]))
            for p in r2["photos"]:
                c.put(f"/api/sessions/{sid}/photos/{p['id']}/pick")
            st2b = c.get(f"/api/sessions/{sid}").json()
            check("第 2 轮 pickCount=2", st2b["pickCount"] == 2, json.dumps(st2b)[:300])
            st3 = c.post(f"/api/sessions/{sid}/rounds/next").json()
            check("进入第 3 轮", st3["roundIndex"] == 3 and st3["roundPhotoCount"] == 2)
            r3 = c.get(f"/api/sessions/{sid}/rounds/3/photos").json()["photos"]
            c.put(f"/api/sessions/{sid}/photos/{r3[0]['id']}/pick")
            r_nx = c.post(f"/api/sessions/{sid}/rounds/next")
            check("第 3 轮仅 pick 1 张时 next 409", expect_error(r_nx, 409, "NOT_ENOUGH_PICKS"), r_nx.text[:200])

        print("\n[8] 结束并生成结果 JSON")
        fin = c.post(f"/api/sessions/{sid}/finish")
        check("POST finish 200", fin.status_code == 200, fin.text[:300])
        result = fin.json()["result"]
        check("phase=finished", c.get(f"/api/sessions/{sid}").json()["phase"] == "finished")
        check("roundsPlayed=3", result["roundsPlayed"] == 3, str(result.get("roundsPlayed")))
        check("结果包含全部照片", len(result["photos"]) == len(expected), f"{len(result['photos'])}")
        by_id = {p["id"]: p for p in result["photos"]}
        check("第一张 pickCount=3 且 pickedInRounds=[1,2,3]",
              by_id[photos[0]["id"]]["pickCount"] == 3 and by_id[photos[0]["id"]]["pickedInRounds"] == [1, 2, 3],
              json.dumps(by_id[photos[0]["id"]]))
        check("第二张 pickCount=2, rounds=[1,2]",
              by_id[photos[1]["id"]]["pickCount"] == 2 and by_id[photos[1]["id"]]["pickedInRounds"] == [1, 2],
              json.dumps(by_id[photos[1]["id"]]))
        check("第三张（被取消）pickCount=0", by_id[photos[2]["id"]]["pickCount"] == 0)
        check("结果按 pickCount 降序", [p["pickCount"] for p in result["photos"]] == sorted((p["pickCount"] for p in result["photos"]), reverse=True))
        check("pickedPhotoCount 正确", result["pickedPhotoCount"] == sum(1 for p in result["photos"] if p["pickCount"] > 0))
        check("finish 幂等", c.post(f"/api/sessions/{sid}/finish").json()["result"]["roundsPlayed"] == 3)
        rn = c.post(f"/api/sessions/{sid}/rounds/next")
        check("finished 后 next 409 PHASE_FINISHED", expect_error(rn, 409, "PHASE_FINISHED"), rn.text[:200])

        print("\n[9] 下载与导出")
        dl = c.get(f"/api/sessions/{sid}/result", params={"download": 1})
        check("下载 200 + attachment", dl.status_code == 200 and "attachment" in dl.headers.get("content-disposition", ""), str(dl.headers))
        check("下载文件名是 .json", ".json" in dl.headers.get("content-disposition", ""))
        exported = out_dir / "pick-result-e2e.json"
        if exported.exists():
            exported.unlink()
        exp = c.post(f"/api/sessions/{sid}/export", json={"path": str(exported)})
        check("export 写文件 200", exp.status_code == 200, exp.text[:300])
        check("导出文件存在且 JSON 可解析", exported.exists() and json.loads(exported.read_text(encoding="utf-8"))["totalPhotos"] == len(expected))
        check("非法导出路径 400 WRITE_FAILED",
              expect_error(c.post(f"/api/sessions/{sid}/export", json={"path": "Z:\\no\\such\\dir\\x.json"}), 400, "WRITE_FAILED"))

        print("\n[9b] 一键导出最后一轮保留照片（后端复制）")
        export_dir = out_dir / "exported-photos"
        if export_dir.exists():
            shutil.rmtree(export_dir)
        export_dir.mkdir(parents=True)
        absolute_paths = {p["name"]: p["absolutePath"] for p in result["photos"]}
        last_round_ids = result["lastRound"]["pickedPhotoIds"]
        last_photos = [p for p in result["photos"] if p["id"] in last_round_ids]
        exp_photos = c.post(f"/api/sessions/{sid}/export-photos", json={"destination": str(export_dir)})
        check("export-photos 200", exp_photos.status_code == 200, exp_photos.text[:300])
        exp_body = exp_photos.json()
        check("copied 等于最后一轮保留数", exp_body["copied"] == len(last_round_ids) and len(last_round_ids) == 1,
              f"{exp_body.get('copied')} vs {len(last_round_ids)}")
        check("files 字段完整", len(exp_body["files"]) == 1 and {"name", "relativePath", "sizeBytes"} <= set(exp_body["files"][0]),
              json.dumps(exp_body)[:300])
        copied_name = exp_body["files"][0]["name"]
        copied_file = export_dir / copied_name
        check("导出文件已写入目标目录", copied_file.is_file(), str(copied_file))
        check("导出文件内容与原图一致", copied_file.read_bytes() == Path(last_photos[0]["absolutePath"]).read_bytes())
        # 同名文件不覆盖：预先放一个同名文件，再次导出应生成 -1 后缀
        copied_file.write_bytes(b"existing-file-must-not-be-overwritten")
        exp_again = c.post(f"/api/sessions/{sid}/export-photos", json={"destination": str(export_dir)})
        check("同名文件自动改名导出", exp_again.status_code == 200 and exp_again.json()["files"][0]["name"] != copied_name,
              exp_again.text[:300])
        check("原有同名文件未被覆盖", copied_file.read_bytes() == b"existing-file-must-not-be-overwritten")
        check("目标目录不存在 400 INVALID_PATH",
              expect_error(c.post(f"/api/sessions/{sid}/export-photos", json={"destination": str(out_dir / "no-such-dir")}),
                           400, "INVALID_PATH"))
        a_file = out_dir / "not-a-dir.txt"
        a_file.write_text("x", encoding="utf-8")
        check("目标路径是文件 400 INVALID_PATH",
              expect_error(c.post(f"/api/sessions/{sid}/export-photos", json={"destination": str(a_file)}),
                           400, "INVALID_PATH"))
        # 没有 pick 就结束的会话 → NO_PICKED_PHOTOS
        no_pick = c.post("/api/sessions", json={"source": "server", "directory": photos_dir, "recursive": True}).json()
        c.post(f"/api/sessions/{no_pick['id']}/finish")
        check("最后一轮无保留照片 400 NO_PICKED_PHOTOS",
              expect_error(c.post(f"/api/sessions/{no_pick['id']}/export-photos", json={"destination": str(export_dir)}),
                           400, "NO_PICKED_PHOTOS"))

        print("\n[10] Web UI 静态资源")
        root = c.get("/")
        check("GET / 200", root.status_code == 200)
        ui_ready = "text/html" in root.headers.get("content-type", "")
        if ui_ready:
            check("index.html 含挂载点", "__PICTUREPICK" in root.text or 'id="app"' in root.text or "<script" in root.text)
        else:
            print("  INFO  前端尚未构建（/ 返回 JSON 提示），静态资源检查跳过")

        print("\n[11] 浏览器来源会话（browser）与一键导出所需字段")
        empty = c.post("/api/sessions", json={"source": "browser", "label": "空", "photos": []})
        check("空照片列表 400 NO_PHOTOS", expect_error(empty, 400, "NO_PHOTOS"), empty.text[:200])
        bad_abs = c.post(
            "/api/sessions",
            json={"source": "browser", "label": "x", "photos": [{"relativePath": "C:/evil.jpg", "sizeBytes": 1}]},
        )
        check("绝对路径 400 INVALID_PHOTOS", expect_error(bad_abs, 400, "INVALID_PHOTOS"), bad_abs.text[:200])
        bad_dotdot = c.post(
            "/api/sessions",
            json={"source": "browser", "label": "x", "photos": [{"relativePath": "../evil.jpg", "sizeBytes": 1}]},
        )
        check("越界路径 400 INVALID_PHOTOS", expect_error(bad_dotdot, 400, "INVALID_PHOTOS"), bad_dotdot.text[:200])
        bad_ext = c.post(
            "/api/sessions",
            json={"source": "browser", "label": "x", "photos": [{"relativePath": "a/b.png", "sizeBytes": 1}]},
        )
        check("非 jpg 扩展名 400 INVALID_PHOTOS", expect_error(bad_ext, 400, "INVALID_PHOTOS"), bad_ext.text[:200])
        dup = c.post(
            "/api/sessions",
            json={
                "source": "browser",
                "label": "去重",
                "photos": [
                    {"relativePath": "a.jpg", "sizeBytes": 10},
                    {"relativePath": "a.jpg", "sizeBytes": 10},
                ],
            },
        )
        check("重复 relativePath 去重后 201 且 1 张", dup.status_code == 201 and dup.json()["libraryTotal"] == 1, dup.text[:300])

        br = c.post(
            "/api/sessions",
            json={"source": "browser", "label": "样例相册", "recursive": True, "photos": browser_photos},
        )
        check("browser 会话 201", br.status_code == 201, br.text[:300])
        bs = br.json()
        bsid = bs["id"]
        check("origin=browser", bs.get("origin") == "browser", json.dumps(bs)[:300])
        check("sourceLabel/root 为文件夹名", bs.get("sourceLabel") == "样例相册" and bs.get("root") == "样例相册", json.dumps(bs)[:300])
        check("libraryTotal 与文件数一致", bs["libraryTotal"] == len(expected), f"{bs['libraryTotal']} vs {len(expected)}")
        bpage = c.get(f"/api/sessions/{bsid}/rounds/1/photos", params={"limit": 1000}).json()
        bphotos = bpage["photos"]
        check("browser 照片列表完整", bpage["total"] == len(expected) and len(bphotos) == len(expected))
        check("browser 照片名为 jpg/jpeg", all(p["name"].lower().endswith((".jpg", ".jpeg")) for p in bphotos))
        check("browser 照片 ID 互不相同", len({p["id"] for p in bphotos}) == len(bphotos))
        img = c.get(f"/api/sessions/{bsid}/photos/{bphotos[0]['id']}/image")
        check("browser 会话图片接口 404 PHOTO_NOT_FOUND", expect_error(img, 404, "PHOTO_NOT_FOUND"), img.text[:200])

        for p in bphotos[:3]:
            r = c.put(f"/api/sessions/{bsid}/photos/{p['id']}/pick")
            check(f"browser pick {p['name']}", r.status_code == 200, r.text[:200])
        bnext = c.post(f"/api/sessions/{bsid}/rounds/next")
        check("browser 进入第 2 轮", bnext.status_code == 200 and bnext.json()["roundPhotoCount"] == 3, bnext.text[:300])
        br2 = c.get(f"/api/sessions/{bsid}/rounds/2/photos").json()["photos"]
        check("第 2 轮为已 pick 的 3 张", {p["id"] for p in br2} == {p["id"] for p in bphotos[:3]})
        for p in br2[:2]:
            c.put(f"/api/sessions/{bsid}/photos/{p['id']}/pick")
        bfin = c.post(f"/api/sessions/{bsid}/finish")
        check("browser finish 200", bfin.status_code == 200, bfin.text[:300])
        bres = bfin.json()["result"]
        check("结果 origin=browser", bres.get("origin") == "browser", str(bres.get("origin")))
        check("结果 sourceDirectory=sourceLabel", bres.get("sourceDirectory") == "样例相册", str(bres.get("sourceDirectory")))
        check("browser 结果 absolutePath 为空串", all(p["absolutePath"] == "" for p in bres["photos"]))
        last = bres.get("lastRound") or {}
        check("lastRound.index=2", last.get("index") == 2, json.dumps(last)[:200])
        check("lastRound.pickedCount=2", last.get("pickedCount") == 2, json.dumps(last)[:200])
        check(
            "lastRound.pickedPhotoIds 为本轮 pick 的照片",
            last.get("pickedPhotoIds") == [br2[0]["id"], br2[1]["id"]],
            json.dumps(last)[:300],
        )
        bby = {p["id"]: p for p in bres["photos"]}
        check(
            "browser pickCount/pickedInRounds 正确",
            bby[bphotos[0]["id"]]["pickCount"] == 2
            and bby[bphotos[0]["id"]]["pickedInRounds"] == [1, 2]
            and bby[bphotos[2]["id"]]["pickCount"] == 1,
            json.dumps({k: bby[k]["pickCount"] for k in [p["id"] for p in bphotos[:3]]}),
        )
        getres = c.get(f"/api/sessions/{bsid}/result")
        check("GET browser result 200 且含 lastRound", getres.status_code == 200 and "lastRound" in getres.json())
        browser_export = c.post(
            f"/api/sessions/{bsid}/export-photos",
            json={"destination": str(export_dir)},
        )
        check("browser 会话导出照片 400 INVALID_SOURCE", expect_error(browser_export, 400, "INVALID_SOURCE"),
              browser_export.text[:200])

        print("\n[12] 载入 JSON 恢复现场（restore）与继续筛选（resume）")
        rsess = c.post("/api/sessions", json={"source": "server", "directory": photos_dir, "recursive": True}).json()
        rsid = rsess["id"]
        check("新建会话 restored=false", rsess.get("restored") is False, json.dumps(rsess)[:200])
        rround1 = c.get(f"/api/sessions/{rsid}/rounds/1/photos", params={"limit": 1000}).json()["photos"]
        for p in rround1[:3]:
            c.put(f"/api/sessions/{rsid}/photos/{p['id']}/pick")
        c.post(f"/api/sessions/{rsid}/rounds/next")
        rround2 = c.get(f"/api/sessions/{rsid}/rounds/2/photos").json()["photos"]
        for p in rround2[:2]:
            c.put(f"/api/sessions/{rsid}/photos/{p['id']}/pick")
        res2 = c.post(f"/api/sessions/{rsid}/finish").json()["result"]
        restore_file = out_dir / "restore-result.json"
        restore_file.write_text(json.dumps(res2, ensure_ascii=False), encoding="utf-8")
        check("finish 结果 lastRound=2 张", res2["lastRound"]["pickedCount"] == 2, json.dumps(res2["lastRound"])[:200])

        restored_resp = c.post("/api/sessions/restore", json=res2)
        check("POST /api/sessions/restore 201", restored_resp.status_code == 201, restored_resp.text[:300])
        restored = restored_resp.json()
        rid = restored["id"]
        check("恢复会话 restored=true", restored.get("restored") is True, json.dumps(restored)[:300])
        check("恢复会话 phase=finished", restored["phase"] == "finished", str(restored.get("phase")))
        check("恢复会话轮次正确", restored["roundIndex"] == 2 and restored["roundCount"] == 2,
              json.dumps({k: restored[k] for k in ("roundIndex", "roundCount")}))
        check("恢复会话库总数=当前扫描数", restored["libraryTotal"] == len(expected),
              f"{restored['libraryTotal']} vs {len(expected)}")
        check("恢复会话来源目录正确", restored["sourceLabel"].lower() == photos_dir.lower(),
              str(restored.get("sourceLabel")))

        restored_result = c.get(f"/api/sessions/{rid}/result").json()
        check("恢复结果 roundsPlayed=2", restored_result["roundsPlayed"] == 2, str(restored_result["roundsPlayed"]))
        check("恢复结果 lastRound.pickedPhotoIds 一致",
              restored_result["lastRound"]["pickedPhotoIds"] == res2["lastRound"]["pickedPhotoIds"],
              json.dumps(restored_result["lastRound"])[:300])
        old_counts = {p["id"]: (p["pickCount"], p["pickedInRounds"]) for p in res2["photos"]}
        new_counts = {p["id"]: (p["pickCount"], p["pickedInRounds"]) for p in restored_result["photos"]}
        check("恢复结果 pickCount/pickedInRounds 一致",
              all(old_counts.get(k) == v for k, v in new_counts.items() if k in old_counts),
              "mismatch")
        check("恢复结果 absolutePath 为绝对路径",
              all(p["absolutePath"] and Path(p["absolutePath"]).is_file() for p in restored_result["photos"]))

        restore_export_dir = out_dir / "restore-export"
        if restore_export_dir.exists():
            shutil.rmtree(restore_export_dir)
        restore_export_dir.mkdir(parents=True)
        rexp = c.post(f"/api/sessions/{rid}/export-photos", json={"destination": str(restore_export_dir)})
        check("恢复会话可导出照片", rexp.status_code == 200 and rexp.json()["copied"] == 2, rexp.text[:300])
        check("恢复导出文件存在",
              all((restore_export_dir / f["name"]).is_file() for f in rexp.json()["files"]))

        resume = c.post(f"/api/sessions/{rid}/resume")
        check("POST resume 200", resume.status_code == 200, resume.text[:300])
        resumed = resume.json()
        rid = resumed["id"]
        check("resume 后 phase=picking", resumed["phase"] == "picking", str(resumed.get("phase")))
        check("resume 后轮次 +1", resumed["roundIndex"] == 3 and resumed["roundCount"] == 3,
              json.dumps({k: resumed[k] for k in ("roundIndex", "roundCount")}))
        check("resume 后新一轮照片=最后保留照片", resumed["roundPhotoCount"] == 2,
              str(resumed.get("roundPhotoCount")))
        round3 = c.get(f"/api/sessions/{rid}/rounds/3/photos").json()["photos"]
        check("第 3 轮照片 ID 与 lastRound 一致",
              [p["id"] for p in round3] == res2["lastRound"]["pickedPhotoIds"],
              json.dumps([p["id"] for p in round3])[:200])
        for p in round3:
            c.put(f"/api/sessions/{rid}/photos/{p['id']}/pick")
        fin3 = c.post(f"/api/sessions/{rid}/finish").json()["result"]
        counts3 = {p["id"]: p["pickCount"] for p in fin3["photos"]}
        check("resume 后再筛一轮，历史 pickCount 累加",
              fin3["roundsPlayed"] == 3
              and all(counts3[p["id"]] == old_counts[p["id"]][0] + 1 for p in round3),
              json.dumps({p["id"]: counts3[p["id"]] for p in round3}))

        wrong_schema = json.loads(json.dumps(res2))
        wrong_schema["schemaVersion"] = 2
        check("restore schemaVersion!=1 400 UNSUPPORTED_SCHEMA",
              expect_error(c.post("/api/sessions/restore", json=wrong_schema), 400, "UNSUPPORTED_SCHEMA"))
        wrong_origin = json.loads(json.dumps(res2))
        wrong_origin["origin"] = "browser"
        check("restore browser 来源 400 INVALID_SOURCE",
              expect_error(c.post("/api/sessions/restore", json=wrong_origin), 400, "INVALID_SOURCE"))
        wrong_dir = json.loads(json.dumps(res2))
        wrong_dir["sourceDirectory"] = "Z:\\no\\such\\dir"
        fallback_resp = c.post("/api/sessions/restore", json=wrong_dir)
        check("restore 源目录不可用但 absolutePath 有效 → 回退 201",
              fallback_resp.status_code == 201, fallback_resp.text[:300])
        bad_photos = json.loads(json.dumps(res2))
        bad_photos["photos"] = [{"name": "x.jpg"}]
        check("restore photos 结构非法 400 INVALID_RESTORE",
              expect_error(c.post("/api/sessions/restore", json=bad_photos), 400, "INVALID_RESTORE"))
        picking_new = c.post("/api/sessions", json={"source": "server", "directory": photos_dir}).json()
        check("picking 会话 resume 409 PHASE_PICKING",
              expect_error(c.post(f"/api/sessions/{picking_new['id']}/resume"), 409, "PHASE_PICKING"))
        check("最后一轮仅 1 张时 resume 409 NOT_ENOUGH_PICKS",
              expect_error(c.post(f"/api/sessions/{sid}/resume"), 409, "NOT_ENOUGH_PICKS"))

        print("\n[13] 旧版 JSON 恢复兼容（缺少 origin/lastRound，absolutePath 回退）")
        legacy = json.loads(json.dumps(res2))
        legacy.pop("origin", None)
        legacy.pop("lastRound", None)
        legacy_resp = c.post("/api/sessions/restore", json=legacy)
        check("旧版 JSON（无 origin/lastRound）restore 201", legacy_resp.status_code == 201, legacy_resp.text[:300])
        if legacy_resp.status_code == 201:
            legacy_session = legacy_resp.json()
            check("旧版恢复 restored=true", legacy_session.get("restored") is True)
            check("旧版恢复轮次与第 2 轮照片数正确",
                  legacy_session["roundIndex"] == 2 and legacy_session["roundPhotoCount"] == 3,
                  json.dumps(legacy_session)[:300])
            legacy_result = c.get(f"/api/sessions/{legacy_session['id']}/result").json()
            check("旧版恢复后 lastRound 与 JSON pickedInRounds 推导一致",
                  legacy_result["lastRound"]["pickedCount"] == 2
                  and set(legacy_result["lastRound"]["pickedPhotoIds"])
                  == {p["id"] for p in res2["photos"] if 2 in p["pickedInRounds"]},
                  json.dumps(legacy_result["lastRound"])[:300])

        renamed_dir = json.loads(json.dumps(res2))
        renamed_dir.pop("origin", None)
        renamed_dir.pop("lastRound", None)
        renamed_dir["sourceDirectory"] = "Z:\\moved-or-renamed-dir\\JPG"
        renamed_resp = c.post("/api/sessions/restore", json=renamed_dir)
        check("旧版 JSON 源目录不可用但 absolutePath 有效 → 回退 restore 201",
              renamed_resp.status_code == 201, renamed_resp.text[:300])
        if renamed_resp.status_code == 201:
            renamed_session = renamed_resp.json()
            check("回退恢复 libraryTotal=可用照片数", renamed_session["libraryTotal"] == len(expected),
                  f"{renamed_session['libraryTotal']} vs {len(expected)}")
            check("回退恢复第 2 轮照片数正确", renamed_session["roundPhotoCount"] == 3,
                  str(renamed_session["roundPhotoCount"]))

        broken = json.loads(json.dumps(res2))
        broken.pop("origin", None)
        broken["sourceDirectory"] = "Z:\\moved-or-renamed-dir\\JPG"
        for photo in broken["photos"]:
            photo["absolutePath"] = "Z:\\moved-or-renamed-dir\\JPG\\" + photo["name"]
        broken_resp = c.post("/api/sessions/restore", json=broken)
        check("旧版 JSON 源目录与 absolutePath 都不可用 400 INVALID_PATH",
              expect_error(broken_resp, 400, "INVALID_PATH"), broken_resp.text[:300])
        check("INVALID_PATH 消息包含源目录原文",
              "moved-or-renamed-dir" in broken_resp.json().get("detail", {}).get("message", ""),
              broken_resp.text[:300])

        # ------------------------------------------------------------------
        # AI 识鸟（docs/BIRDID-CONTRACT.md §6/§7/§9）：需要后端以 mock 模式启动。
        # 采纳会写照片 EXIF，因此先复制照片目录，避免改动原始照片。
        # ------------------------------------------------------------------
        print("\n[14] AI 识鸟：配置 / 状态 / 连通性（mock 模式）")
        try:
            bird_dir = out_dir / "bird-photos"
            if bird_dir.exists():
                shutil.rmtree(bird_dir)
            shutil.copytree(photos_dir, bird_dir)
            bird_expected = sorted(
                p for p in bird_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"}
            )
            check("识鸟用照片副本已创建", len(bird_expected) == len(expected),
                  f"{len(bird_expected)} vs {len(expected)}")

            def poll_task(session_id: str, task_id: str, timeout: float = 30.0) -> dict:
                deadline = time.monotonic() + timeout
                last: dict = {}
                while time.monotonic() < deadline:
                    resp = c.get(f"/api/sessions/{session_id}/bird-tasks/{task_id}")
                    if resp.status_code != 200:
                        return last
                    last = resp.json()
                    if last.get("status") in ("completed", "failed", "adopted", "dismissed"):
                        return last
                    time.sleep(0.1)
                return last

            def jpeg_tail(data: bytes) -> bytes:
                """SOS 段起点到文件尾（图像压缩数据 + EOI），EXIF 改写不应影响它。"""
                pos = data.find(b"\xff\xda")
                return data[pos:] if pos >= 0 else b""

            def xp_text(exif, tag: int) -> str:
                raw = exif.get(tag)
                if isinstance(raw, bytes):
                    return raw.decode("utf-16-le", "replace").rstrip("\x00")
                return str(raw or "")

            status0_resp = c.get("/api/bird/status")
            check("GET /api/bird/status 200", status0_resp.status_code == 200, status0_resp.text[:200])
            status0 = status0_resp.json()
            check("mock 模式 configured=true 且 mock=true",
                  status0.get("configured") is True and status0.get("mock") is True, json.dumps(status0)[:300])
            check("默认模型为 doubao-seed-2-1-lite", status0.get("model") == "doubao-seed-2-1-lite",
                  str(status0.get("model")))
            check("mock 模式 keyMasked=null 且 keySource=mock",
                  status0.get("keyMasked") is None and status0.get("keySource") == "mock", json.dumps(status0)[:300])
            check("status.queue 含 queued/running 计数",
                  isinstance(status0.get("queue"), dict) and {"queued", "running"} <= set(status0["queue"]),
                  json.dumps(status0.get("queue")))
            check("status.endpoint 为完整 Chat Completions 端点",
                  status0.get("endpoint") == "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
                  str(status0.get("endpoint")))
            check("status.endpointProtocol=chat",
                  status0.get("endpointProtocol") == "chat", str(status0.get("endpointProtocol")))
            check("status.endpointWarning=null",
                  status0.get("endpointWarning") is None, str(status0.get("endpointWarning")))

            bad_url = c.put("/api/bird/config", json={"endpoint": "ftp://not-http"})
            check("PUT 非法 endpoint 400 INVALID_CONFIG", expect_error(bad_url, 400, "INVALID_CONFIG"), bad_url.text[:300])
            base_only = c.put("/api/bird/config", json={"endpoint": "https://ark.example.com/api/v3"})
            check("PUT 只填 Base URL（缺少 /chat/completions 或 /responses）400 INVALID_CONFIG",
                  expect_error(base_only, 400, "INVALID_CONFIG"), base_only.text[:300])
            bad_mock = c.put("/api/bird/config", json={"mock": "yes"})
            check("PUT mock 非布尔值 400 INVALID_CONFIG", expect_error(bad_mock, 400, "INVALID_CONFIG"), bad_mock.text[:300])

            saved = c.put("/api/bird/config",
                          json={"endpoint": "https://ark.example.com/api/v3/responses/", "model": "doubao-e2e-model"})
            check("PUT 保存 endpoint/model 200", saved.status_code == 200, saved.text[:300])
            saved_status = saved.json()
            check("保存后 endpoint 去掉尾部斜杠",
                  saved_status.get("endpoint") == "https://ark.example.com/api/v3/responses",
                  str(saved_status.get("endpoint")))
            check("保存后 endpointProtocol=responses",
                  saved_status.get("endpointProtocol") == "responses", str(saved_status.get("endpointProtocol")))
            check("保存后 status 生效（model=自定义）", saved_status.get("model") == "doubao-e2e-model",
                  str(saved_status.get("model")))
            check("GET status 与保存值一致",
                  c.get("/api/bird/status").json().get("model") == "doubao-e2e-model")

            full_key = "ark-e2e-test-1234567890abcdef"
            with_key = c.put("/api/bird/config", json={"apiKey": full_key})
            key_status = with_key.json()
            check("PUT 保存 apiKey 200 且 keySource=config",
                  with_key.status_code == 200 and key_status.get("keySource") == "config", with_key.text[:300])
            check("响应不回传明文 Key", full_key not in with_key.text, with_key.text[:300])
            check("Key 掩码保留首尾（ark-…cdef）",
                  str(key_status.get("keyMasked", "")).startswith("ark-") and str(key_status.get("keyMasked", "")).endswith("cdef"),
                  str(key_status.get("keyMasked")))

            cleared_key = c.put("/api/bird/config", json={"apiKey": ""}).json()
            check("PUT apiKey 空字符串清空 Key",
                  cleared_key.get("keyMasked") is None and cleared_key.get("keySource") == "mock",
                  json.dumps(cleared_key)[:300])
            defaults = c.put("/api/bird/config", json={"endpoint": "", "model": ""}).json()
            check("PUT 清空 endpoint/model 恢复默认值",
                  defaults.get("endpoint") == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
                  and defaults.get("model") == "doubao-seed-2-1-lite",
                  json.dumps(defaults)[:300])

            verify = c.post("/api/bird/verify")
            verify_body = verify.json()
            check("POST /api/bird/verify 200 且 ok=true（mock）",
                  verify.status_code == 200 and verify_body.get("ok") is True, verify.text[:300])
            check("verify 消息为模拟模式", "模拟模式" in str(verify_body.get("message")), str(verify_body.get("message")))
            check("verify 返回默认模型",
                  verify_body.get("model") == "doubao-seed-2-1-lite", str(verify_body.get("model")))

            print("\n[15] AI 识鸟：提交任务与右侧队列")
            bcreated = c.post("/api/sessions",
                              json={"source": "server", "directory": str(bird_dir), "recursive": True})
            check("识鸟用 server 会话 201", bcreated.status_code == 201, bcreated.text[:300])
            bsession = bcreated.json()
            bsid = bsession["id"]
            check("识鸟会话库总数正确", bsession["libraryTotal"] == len(expected),
                  f"{bsession['libraryTotal']} vs {len(expected)}")
            check("识鸟会话初始 birdIdentifiedCount=0", bsession.get("birdIdentifiedCount") == 0,
                  str(bsession.get("birdIdentifiedCount")))
            bpage = c.get(f"/api/sessions/{bsid}/rounds/1/photos", params={"limit": 1000}).json()
            bphotos = bpage["photos"]
            check("识鸟会话照片列表完整", len(bphotos) == len(expected))

            t1_resp = c.post(f"/api/sessions/{bsid}/bird-tasks", json={"photoId": bphotos[0]["id"]})
            check("POST bird-tasks 201", t1_resp.status_code == 201, t1_resp.text[:300])
            task1 = t1_resp.json()
            check("任务初始状态为 queued/running", task1.get("status") in ("queued", "running"),
                  str(task1.get("status")))
            required_task_fields = {
                "id", "sessionId", "photoId", "photoName", "photoRelativePath", "thumbnailUrl",
                "status", "stage", "progress", "queuePosition", "createdAt", "startedAt", "finishedAt",
                "elapsedMs", "model", "result", "error", "adopted", "referenceAvailable", "referenceUrl",
            }
            check("BirdTask 字段完整", required_task_fields <= set(task1), json.dumps(sorted(task1))[:400])
            check("任务 photoId/photoName 正确",
                  task1.get("photoId") == bphotos[0]["id"] and task1.get("photoName") == bphotos[0]["name"],
                  json.dumps({k: task1.get(k) for k in ("photoId", "photoName")}))
            check("任务使用默认模型", task1.get("model") == "doubao-seed-2-1-lite", str(task1.get("model")))

            t2_resp = c.post(f"/api/sessions/{bsid}/bird-tasks", json={"photoId": bphotos[1]["id"]})
            check("提交第二个任务 201", t2_resp.status_code == 201, t2_resp.text[:300])
            task2 = t2_resp.json()
            check("第二个任务排队中且 queuePosition 有效",
                  task2.get("status") == "queued" and task2.get("queuePosition") in (1, 2),
                  json.dumps({k: task2.get(k) for k in ("status", "queuePosition")}))
            dup_resp = c.post(f"/api/sessions/{bsid}/bird-tasks", json={"photoId": bphotos[1]["id"]})
            check("重复提交同照片复用同一 task id",
                  dup_resp.status_code == 201 and dup_resp.json().get("id") == task2.get("id"), dup_resp.text[:300])

            listed = c.get(f"/api/sessions/{bsid}/bird-tasks").json()
            check("GET bird-tasks 返回 2 个任务", len(listed.get("tasks", [])) == 2,
                  json.dumps([t.get("status") for t in listed.get("tasks", [])]))
            listed_counts = {
                "queued": sum(1 for t in listed["tasks"] if t["status"] == "queued"),
                "running": sum(1 for t in listed["tasks"] if t["status"] == "running"),
            }
            check("队列计数与任务状态一致", listed.get("queue") == listed_counts,
                  json.dumps({"queue": listed.get("queue"), "counts": listed_counts}))
            order_priority = [{"running": 0, "queued": 1}.get(t["status"], 2) for t in listed["tasks"]]
            check("任务列表排序：running 在 queued 之前", order_priority == sorted(order_priority),
                  json.dumps([t["status"] for t in listed["tasks"]]))
            status_queue = c.get("/api/bird/status").json().get("queue")
            check("/api/bird/status queue 与任务列表一致", status_queue == listed_counts,
                  json.dumps({"status": status_queue, "list": listed_counts}))

            print("\n[16] AI 识鸟：轮询完成与参考照片")
            task1_done = poll_task(bsid, task1["id"])
            check("轮询单任务到 completed", task1_done.get("status") == "completed", json.dumps(task1_done)[:400])
            check("完成后 stage=done", task1_done.get("stage") == "done", str(task1_done.get("stage")))
            check("完成后 progress=100", task1_done.get("progress") == 100, str(task1_done.get("progress")))
            result1 = task1_done.get("result") or {}
            check("result.identified=true（mock 确定性结果）", result1.get("identified") is True, json.dumps(result1)[:300])
            check("result.species 非空", bool(str(result1.get("species", "")).strip()), str(result1.get("species")))
            check("result 含科/属/拉丁名",
                  all(str(result1.get(key, "")).strip() for key in ("family", "genus", "speciesLatin")),
                  json.dumps({k: result1.get(k) for k in ("family", "genus", "speciesLatin")}))
            confidence = result1.get("confidence")
            check("confidence 在 0-1 之间",
                  isinstance(confidence, (int, float)) and 0 <= confidence <= 1, str(confidence))
            check("mock 结果带模拟标注",
                  "模拟识别结果" in str(result1.get("note", "")) and result1.get("referenceSource") == "Mock Reference",
                  json.dumps({k: result1.get(k) for k in ("note", "referenceSource")}))
            check("referenceAvailable=true 且 referenceUrl 指向 reference 接口",
                  task1_done.get("referenceAvailable") is True
                  and str(task1_done.get("referenceUrl", "")).endswith("/reference"),
                  json.dumps({k: task1_done.get(k) for k in ("referenceAvailable", "referenceUrl")}))
            check("finishedAt 有值且 elapsedMs>0",
                  bool(task1_done.get("finishedAt")) and (task1_done.get("elapsedMs") or 0) > 0,
                  json.dumps({k: task1_done.get(k) for k in ("finishedAt", "elapsedMs")}))

            ref_resp = c.get(f"/api/sessions/{bsid}/bird-tasks/{task1['id']}/reference")
            check("GET 参考照片 200", ref_resp.status_code == 200, ref_resp.text[:200])
            check("参考照片 content-type=image/jpeg",
                  ref_resp.headers.get("content-type", "").startswith("image/jpeg"),
                  str(ref_resp.headers.get("content-type")))
            check("参考照片字节数 > 1000", len(ref_resp.content) > 1000, str(len(ref_resp.content)))
            check("参考照片 Cache-Control: no-store",
                  "no-store" in ref_resp.headers.get("cache-control", "").lower(),
                  str(ref_resp.headers.get("cache-control")))
            check("参考照片是 JPEG 数据", ref_resp.content[:2] == b"\xff\xd8", str(ref_resp.content[:8]))

            print("\n[17] AI 识鸟：采纳 → 写 EXIF（图像数据不变）→ 写入会话")
            from PIL import Image as _PILImage

            photo1_path = bird_dir / Path(bphotos[0]["relativePath"])
            before_bytes = photo1_path.read_bytes()
            tail_before = jpeg_tail(before_bytes)
            with io.BytesIO(before_bytes) as buffer:
                size_before = _PILImage.open(buffer).size
            check("采纳前照片图像数据可定位（SOS）", len(tail_before) > 1000, str(len(tail_before)))

            adopt_resp = c.post(f"/api/sessions/{bsid}/bird-tasks/{task1['id']}/adopt")
            check("POST adopt 200", adopt_resp.status_code == 200, adopt_resp.text[:400])
            adopt_body = adopt_resp.json()
            check("采纳后任务 status=adopted", (adopt_body.get("task") or {}).get("status") == "adopted",
                  str((adopt_body.get("task") or {}).get("status")))
            observation = adopt_body.get("observation") or {}
            check("observation.species 与识别结果一致",
                  observation.get("species") == result1.get("species"),
                  json.dumps({"obs": observation.get("species"), "result": result1.get("species")}, ensure_ascii=False))
            check("observation 含科/属/拉丁名/模型/采纳时间",
                  all(bool(str(observation.get(key, "")).strip()) for key in
                      ("family", "genus", "speciesLatin", "model", "adoptedAt")),
                  json.dumps(observation, ensure_ascii=False)[:400])
            exif_out = adopt_body.get("exif") or {}
            check("exif.written=true 且 path 指向该照片",
                  exif_out.get("written") is True and Path(str(exif_out.get("path", ""))).name == photo1_path.name,
                  json.dumps(exif_out, ensure_ascii=False)[:300])
            check("exif.error 为空", exif_out.get("error") is None, str(exif_out.get("error")))

            species = str(result1.get("species", ""))
            species_latin = str(result1.get("speciesLatin", ""))
            family = str(result1.get("family", ""))
            genus = str(result1.get("genus", ""))
            model_name = str(result1.get("model") or task1_done.get("model") or "")
            with _PILImage.open(photo1_path) as image:
                exif = image.getexif()
                size_after = image.size
                description = str(exif.get(0x010E) or "")
                title = xp_text(exif, 0x9C9B)
                keywords = xp_text(exif, 0x9C9E)
                subject = xp_text(exif, 0x9C9F)
                comment = xp_text(exif, 0x9C9C)
            check("EXIF ImageDescription(0x010E) 含拉丁学名",
                  species_latin in description and "Bird:" in description, description[:200])
            check("EXIF XPTitle(0x9C9B) 含中文种名与拉丁名",
                  species in title and species_latin in title, title[:200])
            check("EXIF XPKeywords(0x9C9E) 含「鸟类」与种名",
                  "鸟类" in keywords and species in keywords, keywords[:200])
            check("EXIF XPSubject(0x9C9F) 含科/属/种",
                  family in subject and genus in subject and species in subject, subject[:200])
            check("EXIF XPComment(0x9C9C) 含模型名与置信度",
                  model_name in comment and "%" in comment, comment[:200])
            after_bytes = photo1_path.read_bytes()
            check("照片图像数据（SOS 之后）逐字节未变",
                  jpeg_tail(after_bytes) == tail_before,
                  f"{len(jpeg_tail(after_bytes))} vs {len(tail_before)}")
            check("照片尺寸未变（未重编码）", size_after == size_before, f"{size_before} -> {size_after}")

            adopt_again = c.post(f"/api/sessions/{bsid}/bird-tasks/{task1['id']}/adopt")
            check("已 adopted 任务再次 adopt 幂等更新",
                  adopt_again.status_code == 200 and (adopt_again.json().get("task") or {}).get("status") == "adopted",
                  adopt_again.text[:400])

            adopted_round_photos = c.get(f"/api/sessions/{bsid}/rounds/1/photos",
                                         params={"limit": 1000}).json()["photos"]
            adopted_photo = next(p for p in adopted_round_photos if p["id"] == bphotos[0]["id"])
            check("采纳后 rounds/1/photos 对应 photo.bird 非空",
                  isinstance(adopted_photo.get("bird"), dict), json.dumps(adopted_photo.get("bird"), ensure_ascii=False)[:300])
            check("photo.bird.species 与采纳结果一致",
                  (adopted_photo.get("bird") or {}).get("species") == species,
                  json.dumps(adopted_photo.get("bird"), ensure_ascii=False)[:300])
            session_after_adopt = c.get(f"/api/sessions/{bsid}").json()
            check("会话 birdIdentifiedCount=1", session_after_adopt.get("birdIdentifiedCount") == 1,
                  str(session_after_adopt.get("birdIdentifiedCount")))

            print("\n[18] AI 识鸟：忽略 / 未完成 / 已采纳等错误分支")
            task2_done = poll_task(bsid, task2["id"])
            check("第二个任务最终 completed", task2_done.get("status") == "completed", str(task2_done.get("status")))
            dismiss2 = c.post(f"/api/sessions/{bsid}/bird-tasks/{task2['id']}/dismiss")
            check("dismiss 200 且状态 dismissed",
                  dismiss2.status_code == 200 and dismiss2.json().get("status") == "dismissed", dismiss2.text[:300])
            dismiss2_again = c.post(f"/api/sessions/{bsid}/bird-tasks/{task2['id']}/dismiss")
            check("dismiss 幂等（已 dismissed 再 dismiss 200）",
                  dismiss2_again.status_code == 200 and dismiss2_again.json().get("status") == "dismissed",
                  dismiss2_again.text[:300])
            adopt_dismissed = c.post(f"/api/sessions/{bsid}/bird-tasks/{task2['id']}/adopt")
            check("对 dismissed 任务 adopt 409 BIRD_TASK_NOT_COMPLETED",
                  expect_error(adopt_dismissed, 409, "BIRD_TASK_NOT_COMPLETED"), adopt_dismissed.text[:300])
            dismiss_adopted = c.post(f"/api/sessions/{bsid}/bird-tasks/{task1['id']}/dismiss")
            check("对 adopted 任务 dismiss 409 BIRD_TASK_ADOPTED",
                  expect_error(dismiss_adopted, 409, "BIRD_TASK_ADOPTED"), dismiss_adopted.text[:300])

            t3 = c.post(f"/api/sessions/{bsid}/bird-tasks", json={"photoId": bphotos[2]["id"]}).json()
            t4 = c.post(f"/api/sessions/{bsid}/bird-tasks", json={"photoId": bphotos[3]["id"]}).json()
            queued_adopt = c.post(f"/api/sessions/{bsid}/bird-tasks/{t4['id']}/adopt")
            check("任务未完成时 adopt 409 BIRD_TASK_NOT_COMPLETED",
                  expect_error(queued_adopt, 409, "BIRD_TASK_NOT_COMPLETED"), queued_adopt.text[:300])
            queued_ref = c.get(f"/api/sessions/{bsid}/bird-tasks/{t4['id']}/reference")
            check("未完成任务获取参考照片 404 BIRD_REFERENCE_UNAVAILABLE",
                  expect_error(queued_ref, 404, "BIRD_REFERENCE_UNAVAILABLE"), queued_ref.text[:300])
            c.post(f"/api/sessions/{bsid}/bird-tasks/{t3['id']}/dismiss")
            c.post(f"/api/sessions/{bsid}/bird-tasks/{t4['id']}/dismiss")

            unknown_task = c.get(f"/api/sessions/{bsid}/bird-tasks/deadbeefdeadbeef")
            check("未知任务 404 BIRD_TASK_NOT_FOUND",
                  expect_error(unknown_task, 404, "BIRD_TASK_NOT_FOUND"), unknown_task.text[:300])
            unknown_adopt = c.post(f"/api/sessions/{bsid}/bird-tasks/deadbeefdeadbeef/adopt")
            check("采纳未知任务 404 BIRD_TASK_NOT_FOUND",
                  expect_error(unknown_adopt, 404, "BIRD_TASK_NOT_FOUND"), unknown_adopt.text[:300])
            unknown_session_tasks = c.get("/api/sessions/nosuchsession/bird-tasks")
            check("未知会话任务列表 404 SESSION_NOT_FOUND",
                  expect_error(unknown_session_tasks, 404, "SESSION_NOT_FOUND"), unknown_session_tasks.text[:300])
            bad_photo_submit = c.post(f"/api/sessions/{bsid}/bird-tasks", json={"photoId": "deadbeefdeadbeef"})
            check("提交不存在的照片 404 PHOTO_NOT_FOUND",
                  expect_error(bad_photo_submit, 404, "PHOTO_NOT_FOUND"), bad_photo_submit.text[:300])

            browser_bird = c.post("/api/sessions", json={
                "source": "browser", "label": "识鸟不支持", "recursive": True,
                "photos": [{"relativePath": "a.jpg", "sizeBytes": 1}],
            }).json()
            browser_bird_photo = c.get(f"/api/sessions/{browser_bird['id']}/rounds/1/photos").json()["photos"][0]
            browser_submit = c.post(f"/api/sessions/{browser_bird['id']}/bird-tasks",
                                    json={"photoId": browser_bird_photo["id"]})
            check("browser 来源会话提交识鸟 400 INVALID_SOURCE",
                  expect_error(browser_submit, 400, "INVALID_SOURCE"), browser_submit.text[:300])

            # mock 失败/无鸟路径：文件名含 notbird → identified=false（契约 §9）
            notbird_dir = out_dir / "bird-notbird"
            if notbird_dir.exists():
                shutil.rmtree(notbird_dir)
            notbird_dir.mkdir(parents=True)
            shutil.copyfile(bird_dir / Path(bphotos[0]["relativePath"]), notbird_dir / "notbird.jpg")
            notbird_session = c.post("/api/sessions",
                                     json={"source": "server", "directory": str(notbird_dir),
                                           "recursive": True}).json()
            notbird_photo = c.get(f"/api/sessions/{notbird_session['id']}/rounds/1/photos").json()["photos"][0]
            notbird_task = c.post(f"/api/sessions/{notbird_session['id']}/bird-tasks",
                                  json={"photoId": notbird_photo["id"]})
            check("notbird 照片提交识鸟 201", notbird_task.status_code == 201, notbird_task.text[:300])
            notbird_done = poll_task(notbird_session["id"], notbird_task.json()["id"])
            check("mock 文件名含 notbird → 任务 completed 但 identified=false",
                  notbird_done.get("status") == "completed"
                  and (notbird_done.get("result") or {}).get("identified") is False,
                  json.dumps(notbird_done, ensure_ascii=False)[:400])
            check("无鸟结果 reason 非空且无参考照片",
                  bool(str((notbird_done.get("result") or {}).get("reason", "")).strip())
                  and notbird_done.get("referenceAvailable") is False,
                  json.dumps(notbird_done, ensure_ascii=False)[:400])
            notbird_adopt = c.post(f"/api/sessions/{notbird_session['id']}"
                                   f"/bird-tasks/{notbird_done.get('id')}/adopt")
            check("无鸟结果采纳 409 BIRD_RESULT_UNAVAILABLE",
                  expect_error(notbird_adopt, 409, "BIRD_RESULT_UNAVAILABLE"), notbird_adopt.text[:300])
            notbird_ref = c.get(f"/api/sessions/{notbird_session['id']}"
                                f"/bird-tasks/{notbird_done.get('id')}/reference")
            check("无鸟结果参考照片 404 BIRD_REFERENCE_UNAVAILABLE",
                  expect_error(notbird_ref, 404, "BIRD_REFERENCE_UNAVAILABLE"), notbird_ref.text[:300])
            notbird_dismiss = c.post(f"/api/sessions/{notbird_session['id']}"
                                     f"/bird-tasks/{notbird_done.get('id')}/dismiss")
            check("无鸟任务可忽略", notbird_dismiss.status_code == 200
                  and notbird_dismiss.json().get("status") == "dismissed", notbird_dismiss.text[:300])
            c.post(f"/api/sessions/{notbird_session['id']}/finish")
            notbird_result = c.get(f"/api/sessions/{notbird_session['id']}/result").json()
            check("无鸟会话结果 birdIdentifiedCount=0 且 photos[].bird 为空",
                  notbird_result.get("birdIdentifiedCount") == 0
                  and all(p.get("bird") is None for p in notbird_result["photos"]),
                  json.dumps(notbird_result.get("birdIdentifiedCount")))

            print("\n[19] AI 识鸟：结束结果 JSON / restore 保留 bird")
            bfin = c.post(f"/api/sessions/{bsid}/finish")
            check("识鸟会话 finish 200", bfin.status_code == 200, bfin.text[:300])
            bres = bfin.json()["result"]
            check("结果 JSON birdIdentifiedCount=1", bres.get("birdIdentifiedCount") == 1,
                  str(bres.get("birdIdentifiedCount")))
            check("结果 schemaVersion 仍为 1", bres.get("schemaVersion") == 1, str(bres.get("schemaVersion")))
            result_by_id = {p["id"]: p for p in bres["photos"]}
            check("结果 JSON 已采纳照片 photos[].bird 非空",
                  isinstance(result_by_id[bphotos[0]["id"]].get("bird"), dict),
                  json.dumps(result_by_id[bphotos[0]["id"]].get("bird"), ensure_ascii=False)[:300])
            check("结果 JSON 照片 bird.species 正确",
                  (result_by_id[bphotos[0]["id"]].get("bird") or {}).get("species") == species,
                  json.dumps(result_by_id[bphotos[0]["id"]].get("bird"), ensure_ascii=False)[:300])
            check("结果 JSON 未识别照片 bird 为 null",
                  all(p.get("bird") is None for pid, p in result_by_id.items() if pid != bphotos[0]["id"]),
                  "存在非已采纳照片带 bird")
            finished_submit = c.post(f"/api/sessions/{bsid}/bird-tasks", json={"photoId": bphotos[4]["id"]})
            check("会话结束后提交识鸟 409 PHASE_FINISHED",
                  expect_error(finished_submit, 409, "PHASE_FINISHED"), finished_submit.text[:300])

            restored_bird = c.post("/api/sessions/restore", json=bres)
            check("带 bird 的结果 JSON restore 201", restored_bird.status_code == 201, restored_bird.text[:300])
            if restored_bird.status_code == 201:
                rbid = restored_bird.json()["id"]
                rs_photos = c.get(f"/api/sessions/{rbid}/rounds/1/photos",
                                  params={"limit": 1000}).json()["photos"]
                rs_photo = next(p for p in rs_photos if p["id"] == bphotos[0]["id"])
                check("restore 后 PhotoItem.bird 仍存在",
                      isinstance(rs_photo.get("bird"), dict), json.dumps(rs_photo.get("bird"), ensure_ascii=False)[:300])
                check("restore 后 birdIdentifiedCount=1",
                      c.get(f"/api/sessions/{rbid}").json().get("birdIdentifiedCount") == 1)
                rs_result = c.get(f"/api/sessions/{rbid}/result").json()
                rs_by_id = {p["id"]: p for p in rs_result["photos"]}
                check("restore 后结果 JSON bird 保留",
                      isinstance(rs_by_id[bphotos[0]["id"]].get("bird"), dict)
                      and rs_result.get("birdIdentifiedCount") == 1,
                      json.dumps(rs_by_id[bphotos[0]["id"]].get("bird"), ensure_ascii=False)[:300])
        finally:
            reset_cfg = c.put("/api/bird/config",
                              json={"apiKey": None, "endpoint": None, "model": None, "mock": None})
            reset_status = reset_cfg.json() if reset_cfg.status_code == 200 else {}
            check("章节结束恢复 mock 配置（Key 已清空、默认模型、mock 生效）",
                  reset_cfg.status_code == 200 and reset_status.get("mock") is True
                  and reset_status.get("keyMasked") is None
                  and reset_status.get("model") == "doubao-seed-2-1-lite",
                  reset_cfg.text[:300])

    print(f"\n===== E2E 结果: PASS={PASS} FAIL={FAIL} =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

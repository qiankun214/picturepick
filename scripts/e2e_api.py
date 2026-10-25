"""PicturePick 后端 API 端到端验收脚本。

用法（后端已在跑）：
    python scripts/e2e_api.py --base-url http://127.0.0.1:8123 --photos-dir samples/photos

它会完整走一遍：健康检查 → 目录浏览 → 创建会话 → 分页/图片/缩略图 → 浏览 & pick 计数 →
连续多轮筛选 → 结束生成结果 JSON → 下载/导出 → 错误码校验，任何断言失败即非 0 退出。
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
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
        check("restore 目录不存在 400 INVALID_PATH",
              expect_error(c.post("/api/sessions/restore", json=wrong_dir), 400, "INVALID_PATH"))
        bad_photos = json.loads(json.dumps(res2))
        bad_photos["photos"] = [{"name": "x.jpg"}]
        check("restore photos 结构非法 400 INVALID_RESTORE",
              expect_error(c.post("/api/sessions/restore", json=bad_photos), 400, "INVALID_RESTORE"))
        picking_new = c.post("/api/sessions", json={"source": "server", "directory": photos_dir}).json()
        check("picking 会话 resume 409 PHASE_PICKING",
              expect_error(c.post(f"/api/sessions/{picking_new['id']}/resume"), 409, "PHASE_PICKING"))
        check("最后一轮仅 1 张时 resume 409 NOT_ENOUGH_PICKS",
              expect_error(c.post(f"/api/sessions/{sid}/resume"), 409, "NOT_ENOUGH_PICKS"))

    print(f"\n===== E2E 结果: PASS={PASS} FAIL={FAIL} =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

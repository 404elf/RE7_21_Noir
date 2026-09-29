"""Two independent Chromium browsers complete a match through the actual Web UI.

python tests/web_e2e.py           # isolated local server, fast test configuration
python tests/web_e2e.py --url ... # an already running server with its own rules
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def run(url, fast=False):
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    failures = []
    with sync_playwright() as p:
        browsers = [p.chromium.launch(), p.chromium.launch()]
        try:
            pages = [b.new_page(viewport={"width": 1440, "height": 1050}) for b in browsers]
            for page in pages:
                page.on("pageerror", lambda error: failures.append(str(error)))
                page.on("console", lambda message: failures.append(message.text) if message.type == "error" else None)
                page.goto(url)
            a, b = pages
            a.screenshot(path=str(output / "web-home.png"), full_page=True)
            a.locator("#nickname").fill("房主 Alice")
            a.locator("#create-room").click()
            a.locator("#ready").wait_for(state="visible")
            expect(a.locator("#ready")).to_be_enabled()
            code = a.locator("#room-label").inner_text()
            b.goto(f"{url}/?room={code}")
            assert b.locator("#room-code").input_value() == code
            b.locator("#nickname").fill("对手 Bob")
            b.locator("#join-room").click()
            expect(b.locator("#ready")).to_be_enabled()
            expect(a.locator("#lobby-players")).to_contain_text("对手 Bob")
            a.locator("#ready").click()
            expect(b.locator("#lobby-players")).to_contain_text("已准备")
            b.locator("#ready").click()
            for page in pages:
                page.locator("#game").wait_for(state="visible")
                assert page.locator("#opponent .hidden-card").count() == 1
            if fast:
                a.locator(".trump-card").first.click()
                a.locator("#use-trump").click()
                expect(a.locator("#my-effects")).to_contain_text("加注")
                assert "轮到你" in a.locator("#turn-hint").inner_text()
                remaining = a.locator(".trump-card").count()
                a.locator(".trump-card").first.click()
                a.locator("#discard-trump").click()
                expect(a.locator(".trump-card")).to_have_count(remaining - 1)
            a.screenshot(path=str(output / "web-table.png"), full_page=True)
            # Reload preserves seat, hidden-card view, and the same match.
            before = b.locator("#my-player .total strong").inner_text()
            b.reload()
            b.locator("#game").wait_for(state="visible")
            expect(b.locator("#connection")).to_contain_text("已连接")
            assert b.locator("#my-player .total strong").inner_text() == before
            deadline = time.monotonic() + 240
            moves = 0
            while time.monotonic() < deadline:
                if a.locator("#rematch").is_visible() and b.locator("#rematch").is_visible():
                    break
                acted = False
                for page in pages:
                    if not page.locator("#stay").is_enabled():
                        continue
                    total = int(page.locator("#my-player .total strong").inner_text())
                    target = int(page.locator("#target").inner_text())
                    button = "#hit" if total < target - 4 and page.locator("#hit").is_enabled() else "#stay"
                    page.locator(button).click()
                    moves += 1
                    acted = True
                    page.wait_for_timeout(570)
                    break
                if not acted:
                    a.wait_for_timeout(100)
            else:
                raise AssertionError("The browsers did not finish a health-based match in 240 seconds")
            assert "本场" in a.locator("#result-title").inner_text()
            assert a.locator("#opponent .hidden-card").count() == 0
            a.screenshot(path=str(output / "web-result.png"), full_page=True)
            a.locator("#rematch").click()
            expect(a.locator("#rematch")).to_contain_text("等待")
            b.wait_for_timeout(300)
            b.locator("#rematch").click()
            expect(a.locator("#rematch")).to_be_hidden()
            expect(a.locator("#round-label")).to_contain_text("01")
            b.set_viewport_size({"width": 390, "height": 844})
            b.screenshot(path=str(output / "web-mobile.png"), full_page=True)
            assert b.evaluate("document.documentElement.scrollWidth <= innerWidth")
            b.locator("#rules-open").click()
            b.locator("#catalog-search").fill("护盾")
            assert b.locator("#catalog article").count() > 0
            b.locator("#rules-close").click()
            assert not failures, failures
            print(f"PASS: two Chromium instances, create/join, private cards, {'trumps/discard, ' if fast else ''}refresh reconnect, {moves} moves, health-based gameover, rematch, mobile and catalog; zero browser errors")
            print(f"Screenshots: {output}")
        finally:
            for browser in browsers:
                browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url")
    args = parser.parse_args()
    if args.url:
        run(args.url.rstrip("/"))
        return
    with tempfile.TemporaryDirectory(prefix="noir-web-e2e-") as temp:
        directory = Path(temp)
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
        config["game_settings"].update(max_hp=2, initial_trumps_count=1, number_card_draw_probability=0)
        config["trump_weights"] = {k: 0 for k, v in config["trump_weights"].items() if isinstance(v, (int, float))}
        config["trump_weights"]["Add 1"] = 1
        (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (directory / "timer.json").write_text('{"enabled":false,"settlement_seconds":0.4}', encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = dict(os.environ, NOIR_CONFIG=str(directory / "config.json"), NOIR_TIMER=str(directory / "timer.json"), NOIR_ORIGIN="")
        with (directory / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", str(port), "--ws-max-size", "2048"], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                url = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError((directory / "server.log").read_text(encoding="utf-8"))
                    try:
                        urllib.request.urlopen(url + "/healthz", timeout=.5).close()
                        break
                    except OSError:
                        time.sleep(.1)
                run(url, fast=True)
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()

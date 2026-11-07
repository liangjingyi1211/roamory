"""Offline Chrome preview with synthetic data; no credentials or live Places requests.

Run with the project Python. Requires Chrome and websockets (already an ADK dependency).
Writes screenshots and layout checks to artifacts/v6/.
"""
import base64
import gc
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect


def main():
    root = Path(__file__).resolve().parents[1]
    frontend = root / "travelmind/frontend"
    host = "--host" in sys.argv
    out = root / ("artifacts/left-maps-host" if host else "artifacts/left-maps")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="travelmind-blue-", ignore_cleanup_errors=True) as directory:
        temporary = Path(directory)
        html = (frontend / "index.html").read_text(encoding="utf-8")
        for name in ("theme.css", "style.css"):
            html = html.replace(f'<link rel="stylesheet" href="{name}">',
                "<style>" + (frontend / name).read_text(encoding="utf-8") + "</style>")
        for name in ("surface.js", "place-details.js", "app.js"):
            html = html.replace(f'<script src="{name}"></script>',
                "<script>" + (frontend / name).read_text(encoding="utf-8") + "</script>")
        days = []
        for number in range(1, 4):
            places, timeline = {}, []
            for index, (name, kind) in enumerate([
                ("Harbour Museum", "activity"), ("Waterfront Lunch", "meal"),
                ("Botanic Gardens", "activity"), ("Evening Gallery", "activity"),
                ("Harbour Hotel", "hotel"),
            ]):
                identity = f"p{number}-{index}"
                places[identity] = dict(id=identity, name=name,
                    address="Sydney Harbour, New South Wales", latitude=None, longitude=None, place_id=None)
                timeline.append(dict(id=f"t{number}-{index}", reference_id=identity, title=name,
                    item_type=kind, meal_type="lunch" if kind == "meal" else None,
                    start_time=f"{9 + index * 2:02}:00", end_time=f"{10 + index * 2:02}:00"))
            days.append(dict(day_id=f"d{number}", day_index=number, date=f"2026-11-{7 + number:02}",
                title="Harbour & city discoveries", places=places, timeline=timeline,
                activities=[places[f"p{number}-{i}"] for i in (0, 2, 3)],
                lunch=places[f"p{number}-1"], hotel=places[f"p{number}-4"], notes=[], timeline_warnings=[]))
        payload = dict(type="streamlit:render", args=dict(
            plan=dict(trip_id="preview", version=1, destination="Sydney", total_days=3, days=days),
            map_message="Preview: Google Maps offline", conversation=[]))
        html = html.replace("</body>", '<script>window.postMessage(' + json.dumps(payload) + ',"*");</script></body>')
        fixture = temporary / "preview.html"
        fixture.write_text(html, encoding="utf-8")
        server = None
        if host:
            # Use a throwaway database; never edit the user's stored journeys.
            sys.path.insert(0, str(root))
            from datetime import date
            from travelmind.models import Activity, Hotel, Restaurant, TripRequest, initialize_plan
            from travelmind.storage import TripStore
            from travelmind.timeline import schedule_day
            plan = initialize_plan(TripRequest(destination="Sydney", start_date=date(2026, 11, 8), end_date=date(2026, 11, 10)))
            for day in plan.days:
                day.activities = [Activity(name=name, address="Sydney Harbour") for name in ("Harbour Museum", "Botanic Gardens", "Evening Gallery")]
                day.breakfast = Restaurant(name="Breakfast Cafe")
                day.lunch = Restaurant(name="Waterfront Lunch")
                day.dinner = Restaurant(name="Dinner Restaurant")
                if day.day_index < 3:
                    day.hotel = Hotel(name="Harbour Hotel")
                schedule_day(day)
            database = temporary / "preview.sqlite3"
            TripStore(database).save(plan)
            gc.collect()
            env = dict(os.environ, TRAVELMIND_DB=str(database), TRAVELMIND_LOG_FILE=str(temporary / "audit.log"), GOOGLE_MAPS_BROWSER_API_KEY="")
            server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(root / "streamlit_app.py"),
                "--server.port=18562", "--server.address=127.0.0.1", "--server.headless=true", "--browser.gatherUsageStats=false"],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            for _ in range(150):
                try:
                    urllib.request.urlopen("http://127.0.0.1:18562/_stcore/health", timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
        process = subprocess.Popen([
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--remote-debugging-port=0", f"--user-data-dir={temporary / 'profile'}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(100):
                try:
                    port = (temporary / "profile/DevToolsActivePort").read_text().splitlines()[0]
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1) as response:
                        tabs = json.load(response)
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("Chrome debugging endpoint did not start")
            with connect(next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page"), max_size=20_000_000) as socket:
                sequence = 0

                def cdp(method, params=None):
                    nonlocal sequence
                    sequence += 1
                    identity = sequence
                    socket.send(json.dumps(dict(id=identity, method=method, params=params or {})))
                    while True:
                        message = json.loads(socket.recv(timeout=15))
                        if message.get("id") == identity:
                            if "error" in message:
                                raise RuntimeError(message["error"])
                            return message.get("result", {})

                context_id = None

                def js(expression):
                    if context_id and host:
                        expression = "((window)=>{const native=window.document,root=native.querySelector('.travelmind-surface'),document={querySelector:s=>root.querySelector(s),querySelectorAll:s=>root.querySelectorAll(s),documentElement:native.documentElement},innerWidth=window.innerWidth,innerHeight=window.innerHeight,scrollY=window.scrollY;return eval(" + json.dumps(expression) + ");})(window.parent)"
                    params = dict(expression=expression, returnByValue=True)
                    if context_id:
                        params["contextId"] = context_id
                    result = cdp("Runtime.evaluate", params)
                    if "exceptionDetails" in result:
                        raise RuntimeError(result["exceptionDetails"])
                    return result["result"].get("value")

                def wait_for(expression):
                    for _ in range(100):
                        if js(expression):
                            return
                        time.sleep(.05)
                    raise AssertionError(expression)

                cdp("Page.enable")
                reports = []
                for width, height in ((1440, 1000), (1024, 768), (768, 1024), (375, 812), (1440, 560), (812, 375)):
                    cdp("Emulation.setDeviceMetricsOverride", dict(width=width, height=height, deviceScaleFactor=1, mobile=False))
                    context_id = None
                    cdp("Page.navigate", dict(url="http://127.0.0.1:18562" if host else fixture.as_uri()))
                    if host:
                        for _ in range(200):
                            if js("!!document.querySelector('.travelmind-surface .event')"):
                                break
                            time.sleep(.1)
                        parent = js("({height:innerHeight, documentHeight:document.documentElement.scrollHeight, mainHeight:document.querySelector('[data-testid=stMain]').scrollHeight, iframeHeight:document.querySelector('iframe').getBoundingClientRect().height, iframeBottom:document.querySelector('iframe').getBoundingClientRect().bottom})")
                        print("host", width, parent)
                        assert parent["iframeBottom"] <= parent["height"] + 2, parent
                        assert parent["iframeHeight"] == 0, parent
                        assert parent["documentHeight"] > parent["height"], parent
                        frame = cdp("Page.getFrameTree")["frameTree"]["childFrames"][0]["frame"]["id"]
                        context_id = cdp("Page.createIsolatedWorld", dict(frameId=frame, worldName="v6-layout-check"))["executionContextId"]
                    for _ in range(100):
                        if js("document.querySelectorAll('.event').length") >= 5:
                            break
                        time.sleep(.05)
                    time.sleep(.25)  # Let iframe sizing and media queries settle.
                    report = js("""({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                        cards:document.querySelectorAll('.event').length,
                        brand:getComputedStyle(document.querySelector('.day.active')).backgroundColor,
                        columns:getComputedStyle(document.querySelector('.geo-column')).gridTemplateColumns,
                        chatHeight:document.querySelector('.assistant').getBoundingClientRect().height,
                        documentHeight:document.documentElement.scrollHeight,
                        viewportHeight:innerHeight,
                        journeyHeight:document.querySelector('.journey-content').clientHeight,
                        journeyScrollHeight:document.querySelector('.journey-content').scrollHeight})""")
                    assert report["scrollWidth"] <= width, report
                    assert report["documentHeight"] > report["viewportHeight"], report
                    assert js("getComputedStyle(document.querySelector('.journey-content')).overflowY") == "visible"
                    assert js("getComputedStyle(document.querySelector('.geo-column')).position") == "sticky"
                    assert js("document.querySelector('.geo-column').getBoundingClientRect().left") == 0
                    if width > 900:
                        assert js("document.querySelector('.geo-column').getBoundingClientRect().right") <= js("document.querySelector('.journey-content').getBoundingClientRect().left")
                        assert abs(js("document.querySelector('.geo-column').getBoundingClientRect().height") - height) < 1
                        assert js("document.querySelector('.map-panel').getBoundingClientRect().bottom") == js("document.querySelector('.place-panel').getBoundingClientRect().top")
                    else:
                        assert js("document.querySelector('.geo-column').getBoundingClientRect().right") == js("document.documentElement.clientWidth")
                    panel_tops = js("[...document.querySelector('.geo-column').children].map(n=>n.getBoundingClientRect().top)")
                    assert js("document.querySelectorAll('.map-heading,.legend,.trip-heading').length") == 0
                    assert js("['.map-panel','.place-panel'].every(s=>{let c=getComputedStyle(document.querySelector(s));return c.borderRadius==='0px'&&c.borderTopWidth==='0px'&&c.boxShadow==='none'})")
                    js("window.scrollTo(0,400)")
                    assert js("window.scrollY") > 0
                    assert js("document.querySelector('.geo-column').getBoundingClientRect().top") == 0
                    assert js("document.querySelector('.assistant').parentElement.id") == "journey-content"
                    js("window.scrollTo(0,document.querySelector('.assistant').getBoundingClientRect().bottom+scrollY-innerHeight+16)")
                    assert js("document.querySelector('#chat-send').getBoundingClientRect().bottom") <= report["viewportHeight"]
                    assert js("document.querySelector('.geo-column').getBoundingClientRect().top") == 0
                    js("window.scrollTo(0,0)")
                    if host:
                        # Real input to the browser's top-level viewport, not a
                        # synthetic scroll event or an iframe's window.scrollY.
                        calendar_top = js("document.querySelector('.days').getBoundingClientRect().top")
                        map_height = js("document.querySelector('.geo-column').getBoundingClientRect().height")
                        x, y = width * .75, min(height - 30, map_height + 100)
                        cdp("Input.dispatchMouseEvent", dict(type="mouseMoved", x=x, y=y))
                        cdp("Input.dispatchMouseEvent", dict(type="mouseWheel", x=x, y=y, deltaX=0, deltaY=400))
                        wait_for("window.scrollY >= 350")
                        time.sleep(.25)  # Finish Chrome's native wheel animation.
                        assert js("document.querySelector('.days').getBoundingClientRect().top") < calendar_top
                        assert js("document.querySelector('.geo-column').getBoundingClientRect().top") == 0
                        assert js("[...document.querySelector('.geo-column').children].map(n=>n.getBoundingClientRect().top)") == panel_tops
                        report["wheelScrollY"] = js("window.scrollY")
                        # Drag the native browser scrollbar thumb to the bottom.
                        js("window.scrollTo({top:0,behavior:'instant'})")
                        wait_for("window.scrollY === 0")
                        time.sleep(.1)
                        scrollbar = js("({width:innerWidth-document.documentElement.clientWidth,thumb:innerHeight*innerHeight/document.documentElement.scrollHeight})")
                        assert scrollbar["width"] > 0, scrollbar
                        x = width - scrollbar["width"] / 2
                        cdp("Input.dispatchMouseEvent", dict(type="mousePressed", x=x, y=scrollbar["thumb"] / 2, button="left", buttons=1, clickCount=1))
                        cdp("Input.dispatchMouseEvent", dict(type="mouseMoved", x=x, y=height - 10, button="left", buttons=1))
                        cdp("Input.dispatchMouseEvent", dict(type="mouseReleased", x=x, y=height - 10, button="left", buttons=0, clickCount=1))
                        time.sleep(.3)
                        print("scrollbar", width, js("({y:window.scrollY,max:document.documentElement.scrollHeight-innerHeight,sendBottom:document.querySelector('#chat-send').getBoundingClientRect().bottom})"))
                        wait_for("window.scrollY >= document.documentElement.scrollHeight-innerHeight-2")
                        report["scrollbarScrollY"] = js("window.scrollY")
                        assert js("document.querySelector('.geo-column').getBoundingClientRect().top") == 0
                        assert js("document.querySelector('#chat-send').getBoundingClientRect().bottom") <= height
                        js("window.scrollTo(0,0)")
                    if host:
                        report["host"] = parent
                    calendar = js("({width:document.querySelector('#days').clientWidth,scrollWidth:document.querySelector('#days').scrollWidth})")
                    if width == 375:
                        assert calendar["scrollWidth"] > calendar["width"], calendar
                    js("window.scrollTo(0,0)")
                    js("document.querySelector('.event .title').click()")
                    assert js("document.querySelector('#place-detail h2').textContent") == ("Breakfast Cafe" if host else "Harbour Museum")
                    assert js("document.querySelectorAll('.event.selected').length") == 1
                    js("document.querySelectorAll('.day')[1].click()")
                    assert "Day 2" in js("document.querySelector('.day.active strong').textContent")
                    js("document.querySelectorAll('.day')[0].click()")
                    js("document.querySelector('.event .title').click()")
                    if host:
                        outer = cdp("Runtime.evaluate", dict(expression="({scroll:document.querySelector('[data-testid=stMain]').scrollTop})", returnByValue=True))["result"]["value"]
                        assert outer["scroll"] == 0, outer
                    image = cdp("Page.captureScreenshot", dict(format="png", captureBeyondViewport=False))
                    (out / f"workspace-{width}-{height}.png").write_bytes(base64.b64decode(image["data"]))
                    reports.append(report)
                (out / "checks.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
                if host:
                    # A real edit crosses the preserved iframe bridge and reruns
                    # Streamlit; the parent-document surface must remain usable.
                    js("document.querySelector('.event .actions button').click()")
                    js("document.querySelector('.edit-form input').value='Edited Breakfast Cafe';document.querySelector('.edit-form').requestSubmit()")
                    wait_for("document.querySelector('.event .title')?.textContent==='Edited Breakfast Cafe'")
                    assert js("document.querySelector('#place-detail h2')===null")
                    assert cdp("Runtime.evaluate", dict(expression="document.querySelectorAll('.travelmind-surface').length", returnByValue=True))["result"]["value"] == 1
                    print("Manual edit / Streamlit rerender / M2 invalidation: passed")
                print(json.dumps(reports, indent=2))
        finally:
            process.terminate()
            process.wait(timeout=10)
            if server:
                server.terminate()
                server.wait(timeout=10)
            # Chrome child processes can take a moment to release the temporary profile.
            time.sleep(1)


if __name__ == "__main__":
    main()

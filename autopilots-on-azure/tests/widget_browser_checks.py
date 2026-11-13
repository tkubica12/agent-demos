"""Real Chromium UI checks against the live local simulator and Agents SDK gateway."""

import asyncio
import json
import re
import shutil
import secrets
import subprocess
import time
from pathlib import Path

import httpx
from microsoft_agents.activity import Activity
from playwright.async_api import async_playwright, expect


async def check_widgets(
    *, simulator, client, call, sim_url, conversation, owner_headers, spec, post_widget,
    render_widget, cipher, session_key, user_id, media_dir, demo, access_code, request_log,
):
    checks = []
    media = Path(media_dir).resolve() if media_dir else None
    if media:
        media.mkdir(parents=True, exist_ok=True)
    raw_dir = media / "raw" / secrets.token_hex(8) if media else None
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        options = {"viewport": {"width": 1280, "height": 800}, "ignore_https_errors": demo}
        context = await browser.new_context(**options)
        page = await context.new_page()
        capture_task = None
        captured = []
        recording = False

        async def capture():
            while recording:
                image = raw_dir / f"frame-{len(captured):04d}.jpg"
                await page.screenshot(path=str(image), type="jpeg", quality=80)
                captured.append(image)
                await asyncio.sleep(.15)
        try:
            await page.goto(sim_url)
            if demo:
                await page.get_by_label("Access code", exact=True).fill(access_code)
                await page.get_by_label("Display name", exact=True).fill("owner")
                await page.get_by_role("button", name="Enter demo", exact=True).click()
            else:
                await page.get_by_test_id("test-login-owner").click()
            await expect(page.get_by_test_id("authenticated")).to_have_attribute("data-connection", "ready")
            await page.locator(f'[data-conversation-id="{conversation}"]').click()

            async def fresh(source=spec):
                activity = render_widget(source, session_key=session_key, user_id=user_id,
                                         conversation_id=conversation, fallback="Choose a plan below.")
                mid = await post_widget(activity)
                locator = page.locator(f'article[data-message-id="{mid}"] iframe')
                await expect(locator).to_be_visible()
                frame = await (await locator.element_handle()).content_frame()
                await expect(frame.locator("#autopilot-status")).to_contain_text("Interactive preview")
                return mid, locator, frame, activity

            if media:
                raw_dir.mkdir(parents=True)
                recording = True
                capture_task = asyncio.create_task(capture())
            mid, iframe, frame, activity = await fresh()
            await expect(iframe).to_have_attribute("sandbox", "allow-scripts")
            await expect(iframe).to_have_attribute("title", spec["title"])
            assert (await iframe.bounding_box())["width"] >= 500
            if media:
                await asyncio.sleep(.8)
            await frame.get_by_label("Budget", exact=True).fill("9")
            await expect(frame.locator("#total")).to_have_text("18")
            await frame.get_by_label("Budget", exact=True).focus()
            await page.keyboard.press("Tab")
            await expect(frame.get_by_role("button", name="Discuss this plan", exact=True)).to_be_focused()
            await page.keyboard.press("Shift+Tab")
            await expect(frame.get_by_label("Budget", exact=True)).to_be_focused()
            await frame.evaluate("window.widgetStateMarker='preserved'")
            await call("POST", f"/api/messages/{mid}/reactions", json={"emoji": "\U0001f44d"})
            await expect(page.locator(f'article[data-message-id="{mid}"]').get_by_test_id("reaction-pills")).to_be_visible()
            assert await frame.evaluate("window.widgetStateMarker") == "preserved"
            await expect(frame.get_by_label("Budget", exact=True)).to_have_value("9")
            await frame.evaluate("document.getElementById('autopilot-root').append(document.createElement('hr'))")
            await asyncio.sleep(.2)
            root_height = await frame.locator("#autopilot-root").evaluate("e=>e.getBoundingClientRect().height")
            frame_height = (await iframe.bounding_box())["height"]
            assert abs(frame_height - root_height) <= 1, (frame_height, root_height)
            checks += ["render", "initialize", "fractional height +/-1px", "local JS recompute",
                       "keyboard focus/title", "state survives message snapshot refresh"]
            if media:
                await asyncio.sleep(.8)
            await frame.get_by_role("button", name="Discuss this plan", exact=True).click()
            await expect(frame.locator("#autopilot-status")).to_contain_text("Selection received")
            await expect(frame.get_by_role("button", name="Discuss this plan", exact=True)).to_be_disabled()
            await expect(page.get_by_text(re.compile(r'Plan received.*"budget": "9"', re.S))).to_be_visible()
            assert await frame.evaluate("window.widgetStateMarker") == "preserved"
            checks += ["real invoke -> accepted", "duplicate button disabled", "same-chat agent follow-up/state retained"]
            if media:
                await asyncio.sleep(.8)
                await page.screenshot(path=str(media / "widget-success.png"))
                recording = False
                await capture_task
                capture_task = None

            raw = json.loads(activity.text.split("```html-widget\n")[1].split("\n```")[0])
            token = re.search(r'"discuss": "([^"]+)"', raw["html"]).group(1)
            forged = {"jsonrpc": "2.0", "id": "forged", "method": "tools/call",
                      "params": {"name": "autopilot.widget.submit", "arguments": {"token": token, "values": {}}}}
            requests = []
            page.on("request", lambda request: requests.append(request.url) if request.url.endswith("/tools") else None)
            await page.evaluate("data=>window.postMessage(data,'*')", forged)
            await asyncio.sleep(.1)
            assert not requests
            checks.append("forged parent-window postMessage ignored")

            _, _, expired_frame, expired_activity = await fresh()
            expired_payload = json.loads(expired_activity.text.split("```html-widget\n")[1].split("\n```")[0])
            valid = re.search(r'"discuss": "([^"]+)"', expired_payload["html"]).group(1)
            expired_claim = json.loads(cipher.decrypt(valid.encode()))
            expired_claim["expiresAtUnix"] = time.time() - 1
            invalid = cipher.encrypt(json.dumps(expired_claim).encode()).decode()
            await expired_frame.evaluate("""token=>{
              const button=document.querySelector('[data-hermes-action]');
              button.dataset.hermesAction='ignored';
              button.addEventListener('click',()=>parent.postMessage({
                jsonrpc:'2.0',id:'selection-expired',method:'tools/call',
                params:{name:'autopilot.widget.submit',arguments:{token,values:{}}}
              },'*'));
              window.addEventListener('message',e=>{
                if(e.data?.id==='selection-expired')document.getElementById('autopilot-status').textContent=
                  e.data.result?.content?.[0]?.text || e.data.error?.message;
              });
            }""", invalid)
            await expired_frame.get_by_role("button", name="Discuss this plan", exact=True).click()
            await expect(expired_frame.locator("#autopilot-status")).to_contain_text("could not be accepted")
            checks.append("real expired Fernet token -> visible isError")

            original_client = simulator.state.channel.client
            for kind in ("500", "timeout", "malformed", "isError"):
                _, _, failed_frame, _ = await fresh()

                async def failed_transport(request, mode=kind):
                    if mode == "timeout":
                        raise httpx.ReadTimeout("Explicit local timeout fixture", request=request)
                    if mode == "500":
                        return httpx.Response(500)
                    if mode == "malformed":
                        return httpx.Response(200, content=b"not JSON")
                    return httpx.Response(200, json={"responseType": "htmlwidget/calltoolresult", "callToolResult": {
                        "content": [{"type": "text", "text": "Explicit widget failure"}],
                        "structuredContent": {}, "isError": True,
                    }})

                fixture_client = httpx.AsyncClient(transport=httpx.MockTransport(failed_transport))
                simulator.state.channel.client = fixture_client
                try:
                    await failed_frame.get_by_role("button", name="Discuss this plan", exact=True).click()
                    await expect(failed_frame.locator("#autopilot-status")).to_contain_text(
                        "Explicit widget failure" if kind == "isError" else "Request failed")
                    await expect(failed_frame.get_by_role("button", name="Discuss this plan", exact=True)).to_be_enabled()
                    checks.append(f"{kind} -> visible error, no hang")
                finally:
                    simulator.state.channel.client = original_client
                    await fixture_client.aclose()

            ordinary = await post_widget(Activity(type="message", text=activity.text, text_format="markdown"))
            await expect(page.locator(f'article[data-message-id="{ordinary}"] pre code')).to_contain_text("widget/mcp-ui")
            await expect(page.locator(f'article[data-message-id="{ordinary}"] iframe')).to_have_count(0)
            checks.append("markdown widget fence stays code")
            users = await call("GET", "/api/users")
            mentioned = next(user for user in users if user.get("isAgent"))
            mention_title = f"<at>{mentioned['displayName']}</at>"
            mention_widget = render_widget({**spec, "title": mention_title}, session_key=session_key,
                                           user_id=user_id, conversation_id=conversation,
                                           fallback=f"Hello {mention_title}")
            mention_widget = Activity.model_validate({
                **mention_widget.model_dump(by_alias=True, exclude_none=True),
                "entities": [{"type": "mention", "mentioned": {"id": mentioned["id"], "name": mentioned["displayName"]},
                              "text": mention_title}],
            })
            mentioned_id = await post_widget(mention_widget)
            mentioned_message = page.locator(f'article[data-message-id="{mentioned_id}"]')
            await expect(mentioned_message.locator("iframe")).to_have_attribute("title", mention_title)
            await expect(mentioned_message.locator(".mention")).to_have_text(mentioned["displayName"])
            checks.append("mention formatting leaves widget JSON/payload unchanged")

            attack_script = """
window.probes={};
const blocked=(name,fn)=>{try{fn();window.probes[name]=false}catch{window.probes[name]=true}};
blocked('parent',()=>parent.document.body.textContent);
blocked('top',()=>top.location.href='https://example.invalid/escape');
blocked('cookies',()=>document.cookie);
blocked('storage',()=>localStorage.getItem('token'));
window.probes.popup=window.open('https://example.invalid/popup')===null;
fetch('/api/me').then(()=>window.probes.fetch=false).catch(()=>window.probes.fetch=true);
window.violations=[];
document.addEventListener('securitypolicyviolation',e=>window.violations.push(e.violatedDirective));
const nested=document.createElement('iframe');nested.src='/api/health';document.body.append(nested);
const form=document.createElement('form');form.action='/api/chats';form.method='post';document.body.append(form);form.submit();
"""
            network_start = len(request_log)
            _, attack_iframe, attack_frame, _ = await fresh({**spec, "javascript": attack_script})
            await asyncio.sleep(.3)
            probes = await attack_frame.evaluate("window.probes")
            assert probes == {"parent": True, "top": True, "cookies": True, "storage": True,
                              "popup": True, "fetch": True}, probes
            violations = await attack_frame.evaluate("window.violations")
            assert any("frame-src" in item for item in violations), violations
            assert len(context.pages) == 1
            assert page.url.startswith(sim_url)
            assert ("POST", "/api/chats") not in request_log[network_start:]
            assert ("GET", "/api/me") not in request_log[network_start:]
            assert ("GET", "/api/health") not in request_log[network_start:]
            checks += ["parent.document blocked", "top navigation blocked", "cookies/localStorage blocked",
                       "API fetch blocked", "window.open blocked", "native form submission blocked", "nested iframe blocked"]
            frame_url = await attack_iframe.get_attribute("src")
            assert (await client.get(frame_url)).status_code == 403
            assert (await client.get(frame_url + "tamper", headers={
                "Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-origin",
            })).status_code == 403
            await attack_frame.evaluate("""() => {
              window.responses=[];
              window.addEventListener('message',e=>{if(e.data?.error)window.responses.push(e.data)});
              for(const data of [
                {jsonrpc:'2.0',id:'unknown',method:'ui/open-link',params:{}},
                {jsonrpc:'2.0',id:'large',method:'tools/call',params:{name:'autopilot.widget.submit',arguments:{x:'x'.repeat(20000)}}},
                {jsonrpc:'1.0',id:'bad',method:'tools/call',params:{}}
              ])parent.postMessage(data,'*');
            }""")
            await asyncio.sleep(.2)
            errors = await attack_frame.evaluate("window.responses")
            assert {item["id"] for item in errors} >= {"unknown", "large", "bad"}, errors
            checks += ["unknown/oversized/invalid RPC errors", "direct/tampered frame URL blocked"]
            await attack_frame.evaluate("""() => {
              for(let id=0;id<12;id++)parent.postMessage({
                jsonrpc:'2.0',id:'flood-'+id,method:'tools/call',
                params:{name:'autopilot.widget.submit',arguments:{token:'invalid',values:{}}}
              },'*');
            }""")
            await asyncio.sleep(.2)
            errors = await attack_frame.evaluate("window.responses")
            assert sum(str(item["id"]).startswith("flood-") for item in errors) == 11, errors
            checks.append("tool-call flood rejected while one request is in flight")

            await attack_frame.evaluate("location.href='https://example.invalid/escape'")
            await asyncio.sleep(.2)
            assert page.url.startswith(sim_url)
            assert not any(frame.url.startswith("https://example.invalid") for frame in page.frames)
            checks.append("self navigation to external origin blocked by parent CSP")
            if media:
                (media / "widget-checks.json").write_text(json.dumps({
                    "browser": "Chromium (installed Microsoft Edge)", "checks": checks,
                    "real": "render_widget, handle_teams_invoke, decode_submission, tool_result, SDK HTTP adapter",
                    "fixtures": "Hermes response/native claim and explicit failed agent transports; no live Azure",
                    "demoFrames": len(captured),
                }, indent=2), encoding="utf-8")
            print(f"PASS Chromium widget browser checks ({len(checks)} assertions/groups)")
        finally:
            recording = False
            if capture_task:
                await capture_task
            await context.close()
            await browser.close()
        if media and captured:
            output = media / "widget-demo.mp4"
            encoder = shutil.which("ffmpeg")
            if encoder:
                subprocess.run([encoder, "-loglevel", "error", "-y", "-framerate", "5",
                                "-i", str(raw_dir / "frame-%04d.jpg"), "-frames:v", str(len(captured)), "-an",
                                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)], check=True)
                subprocess.run([encoder, "-loglevel", "error", "-y", "-i", str(output),
                                "-vf", "fps=8,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                                str(media / "widget-demo.gif")], check=True)
                for image in captured:
                    image.unlink()
                raw_dir.rmdir()

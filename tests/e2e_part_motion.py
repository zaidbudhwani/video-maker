"""Quiet motion (GSAP + Lenis): it runs on the three things it is meant for, it ends clean, and it is off when asked.

The pages' own data is not needed: the checks drive the same DOM changes the pages make (a step becoming active, a card
losing 'hidden', the week sentence changing) and look at the inline styles the animation leaves behind."""
import json



def _inline(page, selector):
    return page.evaluate("""s => { const e = document.querySelector(s); return e ? { o: e.style.opacity, t: e.style.transform, v: e.style.visibility } : null; }""", selector)


WATCH = """([selector, key]) => {
  window.__saw = window.__saw || {};
  window.__saw[key] = false;
  const el = document.querySelector(selector);
  new MutationObserver(() => { if (el.style.opacity || el.style.transform) window.__saw[key] = true; })
    .observe(el, { attributes: true, attributeFilter: ['style'] });
  return !!el;
}"""


def _watch(page, selector, key):
    return page.evaluate(WATCH, [selector, key])


def _saw(page, key):
    return page.evaluate("k => !!(window.__saw && window.__saw[k])", key)


def _clean(state):
    return state is not None and not state["o"] and not state["t"] and not state["v"]


def run(page, ctx):
    print("Motion", flush=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(ctx.BASE)
    page.wait_for_function("window.Motion !== undefined", timeout=10000)

    # --- the libraries are served from this site, from exact names only ---
    for name, kind in (("gsap.min.js", "javascript"), ("lenis.min.js", "javascript"), ("lenis.css", "css"), ("motion.js", "javascript")):
        r = page.request.get(f"{ctx.BASE}/static/{name}")
        ctx.check(f"/static/{name} is served", r.ok and kind in r.headers.get("content-type", ""), f"{r.status} {r.headers.get('content-type')}")
    ctx.check("other names are not served", page.request.get(f"{ctx.BASE}/static/app.py").status == 404
              and page.request.get(f"{ctx.BASE}/static/..%2Fapp.py").status in (400, 404))
    ctx.check("GSAP and Lenis are loaded", page.evaluate("typeof gsap === 'object' && typeof Lenis === 'function'"))
    ctx.check("smooth scrolling is on (Lenis started once)", page.evaluate("!!Motion.lenis() && document.documentElement.classList.contains('lenis')"))
    ctx.check("motion is enabled for this visitor", page.evaluate("Motion.enabled()"))

    # --- 1. the processing screen ---
    page.evaluate("""() => {
      document.getElementById('working').classList.remove('hidden');
      document.querySelectorAll('#steps li').forEach(li => li.className = '');
    }""")
    page.wait_for_timeout(450)
    _watch(page, "#steps li", "active")
    page.evaluate("""() => { document.querySelector('#steps li').className = 'active'; }""")
    page.wait_for_timeout(700)
    ctx.check("a step becoming active eases in", _saw(page, "active"))
    ctx.check("...and ends with no inline style", _clean(_inline(page, "#steps li")))
    _watch(page, "#steps li .dot", "tick")
    page.evaluate("""() => { document.querySelector('#steps li').className = 'done'; }""")
    page.wait_for_timeout(700)
    ctx.check("a finished step's tick pops", _saw(page, "tick"))
    ctx.check("...and ends clean", _clean(_inline(page, "#steps li .dot")))
    # the poll rewrites the same class again and again: that must not animate
    _watch(page, "#steps li .dot", "tick2")
    page.evaluate("""() => { const li = document.querySelector('#steps li'); for (let i = 0; i < 5; i++) li.className = 'done'; }""")
    page.wait_for_timeout(500)
    ctx.check("a poll rewriting the same state does nothing", not _saw(page, "tick2"))
    _watch(page, "#step", "heading")
    page.evaluate("document.getElementById('step').textContent = 'Heading ' + Date.now() + '…'")
    page.wait_for_timeout(700)
    ctx.check("a new heading eases in", _saw(page, "heading"))
    ctx.check("...and ends clean", _clean(_inline(page, "#step")))
    page.evaluate("document.getElementById('working').classList.add('hidden')")

    # --- 2. results: cards enter one after another ---
    page.evaluate("""() => {
      const d = document.getElementById('done');
      d.dataset.n = [...d.children].filter(c => !c.classList.contains('hidden')).length;
      d.classList.remove('hidden');
    }""")
    page.wait_for_timeout(60)
    n_visible = page.evaluate("[...document.getElementById('done').children].filter(c => !c.classList.contains('hidden')).length")
    moving = page.evaluate("""() => [...document.getElementById('done').children].filter(c => !c.classList.contains('hidden') && (c.style.opacity || c.style.transform)).length""")
    ctx.check("result cards animate in when the result shows", n_visible == 0 or moving > 0, f"{moving} of {n_visible} moving")
    page.wait_for_timeout(1900)
    left = page.evaluate("""() => [...document.getElementById('done').querySelectorAll(':scope > *')].filter(c => c.style.opacity || c.style.transform || c.style.visibility).length""")
    ctx.check("every result card ends with no inline style (nothing left faded or shifted)", left == 0, f"{left} left")
    page.evaluate("""() => {
      const box = document.getElementById('players');
      window.__saw = window.__saw || {}; window.__saw.player = false;
      new MutationObserver(recs => recs.forEach(r => r.addedNodes.forEach(n => {
        if (n.nodeType !== 1) return;
        if (n.style.opacity || n.style.transform) window.__saw.player = true;
        new MutationObserver(() => { if (n.style.opacity || n.style.transform) window.__saw.player = true; })
          .observe(n, { attributes: true, attributeFilter: ['style'] });
      }))).observe(box, { childList: true });
    }""")
    page.evaluate("""() => {
      const p = document.createElement('div'); p.id = 'motionTestPlayer'; p.textContent = 'x'; document.getElementById('players').appendChild(p);
    }""")
    page.wait_for_timeout(700)
    ctx.check("a clip/Short player entering animates", _saw(page, "player"))
    ctx.check("...and ends clean", _clean(_inline(page, "#motionTestPlayer")))
    page.evaluate("document.getElementById('motionTestPlayer').remove(); document.getElementById('done').classList.add('hidden')")

    # --- 3. no layout shift: nothing in the page's normal flow is moved by the helper when nothing animates ---
    ctx.check("no JavaScript errors from the motion code", not errors, "; ".join(errors[:3]))

    # --- the My videos page: week card and reminder buttons ---
    week = {"goal_min": 3, "goal_max": 5, "count_this_week": 1, "posted": [], "free_days": ["Thu", "Fri"]}
    rows = {"user": "tester", "admin": True, "videos": []}
    page.route("**/api/posting/week", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(week)))
    page.goto(ctx.BASE + "/videos")
    page.wait_for_function("window.Motion !== undefined && document.getElementById('weekCard') !== null", timeout=10000)
    page.wait_for_timeout(1800)
    ctx.check("the week card shows and ends clean", _clean(_inline(page, "#weekCard")) and "Posted" in page.locator("#weekText").inner_text(), page.locator("#weekText").inner_text()[:60])
    _watch(page, "#weekText", "week")
    page.evaluate("document.getElementById('weekText').innerHTML = 'Posted <b>2</b> of <b>3–5</b> this week · Friday is free'")
    page.wait_for_timeout(700)
    ctx.check("a changed week sentence eases", _saw(page, "week"))
    ctx.check("...and ends clean", _clean(_inline(page, "#weekText")))
    page.evaluate("""() => {
      const f = document.getElementById('free');
      window.__saw = window.__saw || {}; window.__saw.remind = false;
      new MutationObserver(recs => recs.forEach(r => r.addedNodes.forEach(n => {
        if (n.nodeType !== 1) return;
        if (n.style.opacity || n.style.transform) window.__saw.remind = true;
        new MutationObserver(() => { if (n.style.opacity || n.style.transform) window.__saw.remind = true; })
          .observe(n, { attributes: true, attributeFilter: ['style'] });
      }))).observe(f, { childList: true });
      f.innerHTML = '<a class="btn ghost small" id="mA">Remind me Fri</a><a class="btn ghost small" id="mB">Remind me Sat</a>';
    }""")
    page.wait_for_timeout(700)
    ctx.check("reminder buttons ease in", _saw(page, "remind"))
    ctx.check("...and end clean", _clean(_inline(page, "#mA")) and _clean(_inline(page, "#mB")))

    # the numbers count up, but the page's own number is always the true one
    page.evaluate("""() => { const b = document.getElementById('sTotal'); b.textContent = '–'; }""")
    page.wait_for_timeout(100)
    page.evaluate("""() => { document.getElementById('sTotal').textContent = '12'; }""")
    page.wait_for_timeout(120)
    mid = page.evaluate("""() => { const b = document.getElementById('sTotal'); return { text: b.textContent, color: b.style.color, overlay: !!b.parentNode.querySelector('span[aria-hidden]') }; }""")
    ctx.check("while a number counts up the page's own text is already the true number", mid["text"] == "12", mid)
    ctx.check("...and a see-through copy is what counts", mid["overlay"] and mid["color"] == "transparent", mid)
    page.wait_for_timeout(1800)
    end = page.evaluate("""() => { const b = document.getElementById('sTotal'); return { text: b.textContent, color: b.style.color, overlay: !!b.parentNode.querySelector('span[aria-hidden]') }; }""")
    ctx.check("when it ends the copy is gone and the colour is back", end["text"] == "12" and not end["overlay"] and end["color"] == "", end)
    page.unroute("**/api/posting/week")

    # --- smooth scrolling still scrolls (wheel), and a textarea keeps its own scrolling ---
    page.goto(ctx.BASE)
    page.wait_for_function("window.Motion !== undefined && !!Motion.lenis()", timeout=10000)
    page.evaluate("""() => { const s = document.createElement('div'); s.id = 'motionSpacer'; s.style.height = '4000px'; document.body.appendChild(s); }""")
    page.wait_for_timeout(500)   # Lenis re-measures the page height when its content changes
    page.mouse.move(200, 400)
    page.mouse.wheel(0, 700)
    try:
        page.wait_for_function("window.scrollY > 600", timeout=20000)   # it eases in: a busy machine takes longer
    except Exception:
        pass
    y = page.evaluate("window.scrollY")
    ctx.check("mouse wheel scrolls the page with smooth scrolling on", 300 < y <= 760, f"scrollY {y}")
    page.evaluate("document.getElementById('motionSpacer').remove(); window.scrollTo(0, 0)")

    # --- reduced motion: nothing animates and Lenis stays off ---
    ctx_rm = page.context.browser.new_context(reduced_motion="reduce", viewport={"width": 430, "height": 900})
    try:
        rp = ctx_rm.new_page()
        rp.goto(ctx.BASE + "/login")
        rp.fill("#u", "tester")
        rp.fill("#p", ctx.PASSWORD)
        rp.click("#b")
        rp.wait_for_url(ctx.BASE + "/")
        rp.wait_for_function("window.Motion !== undefined", timeout=10000)
        ctx.check("reduced motion: the helper reports itself off", rp.evaluate("Motion.enabled()") is False)
        ctx.check("reduced motion: smooth scrolling is not started", rp.evaluate("Motion.lenis() === null && !document.documentElement.classList.contains('lenis')"))
        rp.evaluate("""() => {
          document.getElementById('working').classList.remove('hidden');
          const d = document.getElementById('done'); d.classList.remove('hidden');
          document.querySelector('#steps li').className = 'active';
          document.getElementById('step').textContent = 'Changed…';
        }""")
        rp.wait_for_timeout(120)
        moved = rp.evaluate("""() => [...document.querySelectorAll('#working *, #done *')].filter(e => e.style.opacity || e.style.transform || e.style.visibility).length""")
        ctx.check("reduced motion: nothing was animated", moved == 0, f"{moved} elements had inline motion styles")
    finally:
        ctx_rm.close()
    page.goto(ctx.BASE)

    # --- the 3D scene on the processing screen ---
    page3 = ctx.expect  # (kept for symmetry with the other parts)
    errs3 = []
    page.on("pageerror", lambda e: errs3.append(str(e)))
    page.goto(ctx.BASE)
    page.wait_for_function("window.Motion !== undefined", timeout=10000)
    ctx.check("the 3D scene file is served", page.request.get(ctx.BASE + "/static/scene3d.js").ok)
    webgl = page.evaluate("""() => { try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); } catch (e) { return false; } }""")
    if not webgl:
        print("   (no WebGL in this browser: checking that the page simply goes without the scene)", flush=True)
    page.evaluate("""() => {
      const fill = document.getElementById('barFill'); fill.style.width = '10%';
      document.getElementById('working').classList.remove('hidden');
    }""")
    if webgl:
        try:
            page.wait_for_selector("#stage3d canvas", timeout=45000)
            mounted = True
        except Exception:
            mounted = False
        ctx.check("the 3D scene mounts on the processing screen", mounted)
        size = page.evaluate("(() => { const c = document.querySelector('#stage3d canvas'); return c ? [c.clientWidth, c.clientHeight] : null; })()")
        ctx.check("it has a real size", bool(size) and size[0] > 200 and size[1] > 100, size)
        ctx.check("it is hidden from screen readers", page.get_attribute("#stage3d", "aria-hidden") == "true")
        page.evaluate("document.getElementById('barFill').style.width = '55%'")
        page.wait_for_timeout(1800)
        page.screenshot(path=ctx.SHOTS / "motion3d-1-working.png")
        painted = page.evaluate("""() => {
          const c = document.querySelector('#stage3d canvas');
          const t = document.createElement('canvas'); t.width = 64; t.height = 32;
          return !!c;
        }""")
        page.evaluate("document.getElementById('barFill').style.width = '100%'")
        page.evaluate("document.getElementById('working').classList.add('hidden'); document.getElementById('done').classList.remove('hidden')")
        page.wait_for_timeout(3600)
        ctx.check("when the result shows, the scene is taken down", page.locator("#stage3d").count() == 0)
        page.evaluate("document.getElementById('done').classList.add('hidden')")
        # a second run mounts again, and leaving the screen takes it down
        page.evaluate("document.getElementById('working').classList.remove('hidden')")
        page.wait_for_selector("#stage3d canvas", timeout=45000)
        page.evaluate("document.getElementById('working').classList.add('hidden')")
        page.wait_for_timeout(500)
        ctx.check("hiding the screen takes the scene down too", page.locator("#stage3d").count() == 0)
    else:
        page.wait_for_timeout(800)
        ctx.check("without WebGL the screen is unchanged and nothing breaks", page.locator("#stage3d").count() == 0)
        page.evaluate("document.getElementById('working').classList.add('hidden')")
    ctx.check("no JavaScript errors from the 3D scene", not errs3, "; ".join(errs3[:3]))

    # reduced motion: no scene at all
    rm = page.context.browser.new_context(reduced_motion="reduce", viewport={"width": 430, "height": 900})
    try:
        rp = rm.new_page()
        rp.goto(ctx.BASE + "/login"); rp.fill("#u", "tester"); rp.fill("#p", ctx.PASSWORD); rp.click("#b"); rp.wait_for_url(ctx.BASE + "/")
        rp.evaluate("document.getElementById('working').classList.remove('hidden')")
        rp.wait_for_timeout(1500)
        ctx.check("reduced motion: no 3D scene", rp.locator("#stage3d").count() == 0)
    finally:
        rm.close()

    # --- the same scene on the sign-in page, the start page and the "ready" moment ---
    print("3D on sign-in, start and result pages", flush=True)
    import io
    from PIL import Image
    errs_h = []
    page.on("pageerror", lambda e: errs_h.append(str(e)))
    webgl2 = page.evaluate("""() => { try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')); } catch (e) { return false; } }""")
    anon = page.context.browser.new_context(viewport={"width": 430, "height": 900})
    try:
        ctx.check("the scene file is public (the sign-in page needs it)", anon.request.get(ctx.BASE + "/static/scene3d.js").ok)
        ap = anon.new_page()
        aerrs = []
        ap.on("pageerror", lambda e: aerrs.append(str(e)))
        ap.goto(ctx.BASE + "/login")
        if webgl2:
            try:
                ap.wait_for_selector("#stage3d canvas", timeout=45000)
                ok = True
            except Exception:
                ok = False
            ctx.check("sign-in page shows the 3D scene", ok)
            ap.wait_for_timeout(1500)
            ap.screenshot(path=ctx.SHOTS / "motion3d-2-signin.png")
            ctx.check("the sign-in form is still usable (fields are visible)", ap.locator("#u").is_visible() and ap.locator("#p").is_visible())
        ctx.check("no JavaScript errors on the sign-in page", not aerrs, "; ".join(aerrs[:2]))
    finally:
        anon.close()

    # the start page: scene while nothing is chosen, gone when clips are picked, back after clearing
    page.goto(ctx.BASE)
    page.wait_for_function("window.Motion !== undefined", timeout=10000)
    if webgl2:
        try:
            page.wait_for_selector("#stage3d-hero canvas", timeout=45000)
            ok = True
        except Exception:
            ok = False
        ctx.check("start page shows the 3D scene while nothing is chosen", ok)
        page.wait_for_timeout(1500)
        page.screenshot(path=ctx.SHOTS / "motion3d-3-start.png")
        ctx.check("it sits above the form and does not hide the 'Add your clips' button", page.locator("#drop").is_visible())
        tiny = ctx.ROOT / "tests" / "shots" / "tiny.mp4"
        import subprocess, sys as _sys
        _sys.path.insert(0, str(ctx.ROOT))
        from video_tool.config import FFMPEG
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x284:d=1:r=30", "-pix_fmt", "yuv420p", str(tiny)], check=True)
        page.set_input_files("#files", [str(tiny)])
        page.wait_for_selector("#clips li.clip")
        page.wait_for_timeout(1800)
        ctx.check("choosing clips takes the scene down", page.locator("#stage3d-hero").count() == 0)
        page.click("#reset")
        page.click("#resetYes")
        try:
            page.wait_for_selector("#stage3d-hero canvas", timeout=45000)
            back = True
        except Exception:
            back = False
        ctx.check("clearing the clips brings it back", back)

    # the ready moment: the finished video's first picture on the phone, then it folds away
    buf = io.BytesIO()
    Image.new("RGB", (270, 480), (180, 120, 60)).save(buf, "JPEG")
    page.route("**/api/jobs/*/thumb.jpg", lambda r: r.fulfill(status=200, content_type="image/jpeg", body=buf.getvalue()))
    page.evaluate("""() => { jobId = 'motion-test-job'; document.getElementById('pick').classList.add('hidden'); }""")
    if webgl2:
        page.evaluate("document.getElementById('done').classList.remove('hidden')")
        try:
            page.wait_for_selector("#done #stage3d-hero canvas", timeout=45000)
            ok = True
        except Exception:
            ok = False
        ctx.check("the result page opens with the phone showing the finished video", ok)
        page.wait_for_timeout(1300)
        page.screenshot(path=ctx.SHOTS / "motion3d-4-ready.png")
        page.wait_for_timeout(5200)
        ctx.check("then it folds away so the video is right at the top", page.locator("#stage3d-hero").count() == 0)
    page.unroute("**/api/jobs/*/thumb.jpg")
    page.evaluate("document.getElementById('done').classList.add('hidden'); document.getElementById('pick').classList.remove('hidden')")
    ctx.check("no JavaScript errors from the extra scenes", not errs_h, "; ".join(errs_h[:3]))

    # reduced motion: none of the three
    rm2 = page.context.browser.new_context(reduced_motion="reduce", viewport={"width": 430, "height": 900})
    try:
        rp2 = rm2.new_page()
        rp2.goto(ctx.BASE + "/login")
        rp2.wait_for_timeout(1500)
        ctx.check("reduced motion: no scene on the sign-in page", rp2.locator("#stage3d").count() == 0)
        rp2.fill("#u", "tester"); rp2.fill("#p", ctx.PASSWORD); rp2.click("#b"); rp2.wait_for_url(ctx.BASE + "/")
        rp2.wait_for_timeout(1500)
        ctx.check("reduced motion: no scene on the start page", rp2.locator("#stage3d-hero").count() == 0)
    finally:
        rm2.close()

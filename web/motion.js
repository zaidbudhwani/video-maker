/* Quiet motion for the Video Maker. One file; delete it and its <script> tags and the site is exactly as before.
 *
 * What it does (and only this):
 *   1. Processing screen: the heading, the step that becomes active and the tick of a finished step.
 *   2. Results: cards and Short/clip players enter one after another when a result loads, and when the clip list fills.
 *   3. Calendar: the week card, its sentence and its reminder buttons when they change.
 *   plus Lenis smooth wheel scrolling, driven by the GSAP ticker (touch scrolling stays the phone's own).
 *
 * It never changes layout, text or data: it watches the page (MutationObserver) and tweens what is already there.
 * Everything is off when the visitor asks for reduced motion, and it does nothing if the libraries did not load.
 * Every tween ends by clearing its inline styles, so an element can never be left faded or shifted.
 */
(function () {
  "use strict";
  var reduce = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  var lenis = null;

  function can() { return !reduce.matches && !!window.gsap; }

  /* ---------- helpers (reusable: Motion.enter / Motion.pop / Motion.swap) ---------- */
  function list(x) { return Array.prototype.slice.call(x && x.length !== undefined && !x.nodeType ? x : [x]).filter(Boolean); }

  function settle(els) {   // however a tween ended, leave nothing inline
    window.setTimeout(function () {
      if (window.gsap) window.gsap.set(els, { clearProps: "opacity,visibility,transform" });
    }, 1600);
  }

  function enter(els, opts) {
    els = list(els);
    if (!can() || !els.length) return;
    var o = opts || {};
    els = els.slice(0, o.max || 14);   // a long list enters as a group; the rest simply appear
    window.gsap.fromTo(els, { opacity: 0, y: o.y == null ? 14 : o.y }, {
      opacity: 1, y: 0, duration: o.duration || 0.5, ease: "expo.out", stagger: o.stagger == null ? 0.06 : o.stagger,
      overwrite: "auto", clearProps: "opacity,visibility,transform", delay: o.delay || 0
    });
    settle(els);
  }

  function pop(el) {   // a finished step's tick
    if (!can() || !el) return;
    window.gsap.fromTo(el, { scale: 0.55 }, { scale: 1, duration: 0.45, ease: "back.out(2.4)", overwrite: "auto", clearProps: "transform" });
    // a soft ring that opens around the tick
    window.gsap.fromTo(el, { boxShadow: "0 0 0 0 rgba(108,174,223,.6)" }, { boxShadow: "0 0 0 12px rgba(108,174,223,0)", duration: 0.7, ease: "power2.out", clearProps: "boxShadow" });
    settle([el]);
  }

  function swap(el) {   // text that just changed
    if (!can() || !el) return;
    window.gsap.fromTo(el, { opacity: 0.25, y: 6 }, { opacity: 1, y: 0, duration: 0.38, ease: "expo.out", overwrite: "auto", clearProps: "opacity,transform" });
    settle([el]);
  }

  /* ---------- a little CSS of our own: the bar's glow and shine, and the stage for the 3D scene ---------- */
  function addStyle() {
    if (document.getElementById("motion-style")) return;
    var st = document.createElement("style");
    st.id = "motion-style";
    st.textContent =
      "#stage3d{height:210px;margin:0 0 10px;border-radius:14px;overflow:hidden;position:relative;" +
      "background:radial-gradient(120% 90% at 50% 0%,var(--accent-soft),transparent 70%)}" +
      "#stage3d canvas{position:absolute;inset:0}" +
      "@media (prefers-reduced-motion:no-preference){" +
      ".bar i{box-shadow:0 0 14px rgba(108,174,223,.55);position:relative;overflow:hidden}" +
      ".bar i::after{content:'';position:absolute;inset:0;background:linear-gradient(100deg,transparent 20%,rgba(255,255,255,.45) 50%,transparent 80%);" +
      "transform:translateX(-100%);animation:motion-shine 1.9s ease-in-out infinite}" +
      "@keyframes motion-shine{to{transform:translateX(100%)}}}";
    document.head.appendChild(st);
  }

  /* ---------- the 3D scene: her clips orbit a phone and are drawn in as the video is made ---------- */
  var stage = null, scene = null, loading = false;
  function lowPower() {
    var c = navigator.connection;
    return (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2) || (c && c.saveData);
  }
  function mountScene() {
    if (!can() || loading || scene || lowPower()) return;
    var working = $("working");
    if (!working) return;
    loading = true;
    import("/static/scene3d.js").then(function (mod) {
      loading = false;
      if (!shown(working) || scene) return;
      stage = document.createElement("div");
      stage.id = "stage3d";
      stage.setAttribute("aria-hidden", "true");
      working.insertBefore(stage, working.firstChild);
      scene = mod.mount(stage);
      if (!scene) { stage.remove(); stage = null; return; }
      var fill = $("barFill");
      if (fill) scene.setProgress((parseFloat(fill.style.width) || 0) / 100);
    }).catch(function () { loading = false; });
  }
  function endScene(finish) {
    if (!scene) return;
    var s = scene, st = stage;
    scene = null; stage = null;
    if (finish) {
      s.setDone();
      window.setTimeout(function () { s.dispose(); if (st && st.parentNode) st.parentNode.removeChild(st); }, 2600);
    } else {
      s.dispose();
      if (st && st.parentNode) st.parentNode.removeChild(st);
    }
  }

  /* ---------- the same scene in two more places: the start page while nothing is chosen, and the "ready" moment ---------- */
  var hero = null, heroLoading = false;
  function removeHero(animated) {
    if (!hero) return;
    var h = hero;
    hero = null;
    var finish = function () {
      try { h.scene.dispose(); } catch (e) { /* already gone */ }
      if (h.stage.parentNode) h.stage.parentNode.removeChild(h.stage);
    };
    if (animated && window.gsap && can()) {
      window.gsap.to(h.stage, { height: 0, marginBottom: 0, opacity: 0, duration: 0.45, ease: "power2.inOut", onComplete: finish });
      window.setTimeout(finish, 1200);
    } else finish();
  }
  function placeHero(parent, opts, then) {
    if (!can() || hero || heroLoading || lowPower()) return;
    heroLoading = true;
    import("/static/scene3d.js").then(function (mod) {
      heroLoading = false;
      if (hero || !parent.isConnected) return;
      var stage = document.createElement("div");
      stage.id = "stage3d-hero";
      stage.setAttribute("aria-hidden", "true");
      stage.style.cssText = "height:" + (opts.height || 180) + "px;margin:0 0 12px;border-radius:14px;overflow:hidden;position:relative;" +
        "background:radial-gradient(120% 90% at 50% 0%,var(--accent-soft),transparent 70%)";
      parent.insertBefore(stage, parent.firstChild);
      var s = mod.mount(stage, { kind: opts.kind, idle: !!opts.idle });
      if (!s) { stage.remove(); return; }
      hero = { stage: stage, scene: s };
      if (then) then(s);
    }).catch(function () { heroLoading = false; });
  }
  function startPageHero() {   // upload page, while no clips are chosen
    var pick = $("pick"), list = $("list");
    if (!pick || !list || shown(list) || shown($("working")) || shown($("done"))) return;
    // in the opening, beside the promise (desktop) or as a short strip under it (phone), so the upload stays on the first screen
    var stage = $("heroStage");
    placeHero(stage || pick, { kind: "flow", height: stage ? (window.innerWidth >= 760 ? 220 : 108) : 180 });
  }
  function readyHero() {       // result page: her finished video's first picture on the phone, spinning into place
    var doneCard = $("done");
    if (!doneCard || typeof jobId === "undefined" || !jobId || shown($("exampleBar"))) return;
    removeHero(false);
    placeHero(doneCard, { kind: "ready", height: 210 }, function (s) {
      s.setPoster("/api/jobs/" + jobId + "/thumb.jpg");
      s.setDone();
      window.setTimeout(function () { removeHero(true); }, 3800);
    });
  }

  /* ---------- numbers that count up the first time they show ---------- */
  function countUp(el) {
    // The number in the page is always the true one (screen readers, copy/paste and tests read it). The counting is a
    // see-through copy laid over it that is removed when it ends.
    if (!can() || !el || !el.parentNode) return;
    var m = /^(\D*)(\d+(?:\.\d+)?)(.*)$/.exec(el.textContent.trim());
    if (!m || parseFloat(m[2]) === 0) return;
    var to = parseFloat(m[2]), decimals = (m[2].split(".")[1] || "").length;
    var parent = el.parentNode, cs = window.getComputedStyle(el), color = el.style.color, pos = parent.style.position;
    var ov = document.createElement("span");
    ov.setAttribute("aria-hidden", "true");
    ov.style.cssText = "position:absolute;white-space:nowrap;pointer-events:none;font:" + cs.font + ";color:" + cs.color +
      ";left:" + el.offsetLeft + "px;top:" + el.offsetTop + "px";
    ov.textContent = m[1] + (0).toFixed(decimals) + m[3];
    if (window.getComputedStyle(parent).position === "static") parent.style.position = "relative";
    parent.appendChild(ov);
    el.style.color = "transparent";
    var o = { v: 0 }, ended = false;
    function end() {
      if (ended) return;
      ended = true;
      el.style.color = color;
      parent.style.position = pos;
      if (ov.parentNode) ov.parentNode.removeChild(ov);
    }
    window.gsap.to(o, {
      v: to, duration: 0.9, ease: "power2.out",
      onUpdate: function () { ov.textContent = m[1] + o.v.toFixed(decimals) + m[3]; },
      onComplete: end, onInterrupt: end
    });
    window.setTimeout(end, 1500);
  }

  /* ---------- Lenis, once, on the GSAP ticker ---------- */
  function startLenis() {
    if (lenis || reduce.matches || !window.Lenis || !window.gsap) return;
    try {
      lenis = new window.Lenis({ lerp: 0.14, smoothWheel: true, allowNestedScroll: true, anchors: true });
      window.gsap.ticker.add(function (t) { if (lenis) lenis.raf(t * 1000); });
      window.gsap.ticker.lagSmoothing(0);
    } catch (e) { lenis = null; }
  }
  function stopLenis() {
    if (!lenis) return;
    try { lenis.destroy(); } catch (e) { /* nothing to undo */ }
    lenis = null;
  }
  function onPreferenceChange() {
    if (reduce.matches) { stopLenis(); if (window.gsap) window.gsap.globalTimeline.clear(); } else startLenis();
  }
  if (reduce.addEventListener) reduce.addEventListener("change", onPreferenceChange);

  /* ---------- what to watch ---------- */
  function $(id) { return document.getElementById(id); }
  function shown(el) { return !!el && !el.classList.contains("hidden"); }

  // An element that just lost 'hidden' (screens and cards are shown by removing it).
  function watchReveal(el, fn) {
    if (!el) return;
    var was = shown(el);
    new MutationObserver(function () {
      var now = shown(el);
      if (now && !was) fn(el);
      was = now;
    }).observe(el, { attributes: true, attributeFilter: ["class"] });
  }
  function watchChildren(el, fn) {
    if (!el) return;
    new MutationObserver(function (recs) {
      var added = [];
      recs.forEach(function (r) { for (var i = 0; i < r.addedNodes.length; i++) if (r.addedNodes[i].nodeType === 1) added.push(r.addedNodes[i]); });
      if (added.length) fn(added);
    }).observe(el, { childList: true });
  }

  function init() {
    startLenis();
    addStyle();

    /* 1. processing screen (index.html) */
    var working = $("working");
    watchReveal(working, function (el) {
      mountScene();
      enter([el.querySelector("h2"), el.querySelector(".bar"), el.querySelector(".steps")], { stagger: 0.08, y: 10 });
    });
    if (working) {
      new MutationObserver(function () { if (!shown(working) && scene) endScene(false); })
        .observe(working, { attributes: true, attributeFilter: ["class"] });
    }
    var fillBar = $("barFill");
    if (fillBar) {
      new MutationObserver(function () { if (scene) scene.setProgress((parseFloat(fillBar.style.width) || 0) / 100); })
        .observe(fillBar, { attributes: true, attributeFilter: ["style"] });
    }
    watchReveal($("failed"), function () { endScene(false); });
    // the start page's scene: there while no clips are chosen, gone once she picks some, back after "Start over"
    var startList = $("list");
    if (startList) {
      new MutationObserver(function () { if (shown(startList)) removeHero(true); else startPageHero(); })
        .observe(startList, { attributes: true, attributeFilter: ["class"] });
      startPageHero();
    }
    watchReveal(working, function () { removeHero(false); });
    var steps = $("steps");
    if (steps) {
      var seen = new WeakMap();   // li -> last state, so a poll that rewrites the same class does nothing
      var read = function (li) { return li.classList.contains("done") ? "done" : li.classList.contains("active") ? "active" : ""; };
      Array.prototype.forEach.call(steps.children, function (li) { seen.set(li, read(li)); });
      new MutationObserver(function (recs) {
        recs.forEach(function (r) {
          var li = r.target.nodeType === 1 && r.target.parentNode === steps ? r.target : null;
          if (!li) { Array.prototype.forEach.call(steps.children, function (c) { if (!seen.has(c)) seen.set(c, read(c)); }); return; }
          var now = read(li), before = seen.get(li);
          seen.set(li, now);
          if (now === before) return;
          if (now === "done") pop(li.querySelector(".dot"));
          else if (now === "active") swap(li);
        });
      }).observe(steps, { attributes: true, attributeFilter: ["class"], subtree: true, childList: true });
    }
    var heading = $("step");
    if (heading) {
      var last = heading.textContent;
      new MutationObserver(function () {
        if (heading.textContent !== last) { last = heading.textContent; swap(heading); }
      }).observe(heading, { childList: true, characterData: true, subtree: true });
    }
    watchReveal($("safeLine"), function (el) { enter(el, { y: 4 }); });

    /* 1b. a quiet scene of its own on My videos (the weekly goal) and on the Team page (a results skyline) */
    var slot = document.querySelector("[data-scene3d]");
    if (slot) placeHero(slot, { kind: slot.getAttribute("data-scene3d"), height: 150 });

    /* 2. results (index.html) */
    var done = $("done");
    watchReveal(done, function (el) {
      endScene(false);
      readyHero();
      var cards = Array.prototype.filter.call(el.children, function (c) { return !c.classList.contains("hidden"); });
      enter(cards, { stagger: 0.045 });
    });
    watchChildren($("players"), function (nodes) { enter(nodes, { stagger: 0.06, y: 10 }); });
    watchChildren($("reorderList"), function (nodes) { enter(nodes, { stagger: 0.03, y: 6 }); });

    /* 2b. the My videos list, first time it fills */
    var listBox = $("list");
    if (listBox) {
      var filled = !!listBox.querySelector(".row, .vid, .item, .card:not(.empty)");
      new MutationObserver(function () {
        var rows = Array.prototype.slice.call(listBox.children).filter(function (c) { return !c.classList.contains("empty"); });
        if (!filled && rows.length) { filled = true; enter(rows, { stagger: 0.035, max: 12 }); }
        if (!rows.length) filled = false;
      }).observe(listBox, { childList: true });
    }

    /* 2c. the My videos numbers count up the first time they show */
    Array.prototype.forEach.call(document.querySelectorAll(".stat b"), function (b) {
      var was = b.textContent, numeric = function (t) { return /^\D*\d/.test(t); };
      new MutationObserver(function () {
        var now = b.textContent;
        var first = !numeric(was) && numeric(now);
        was = now;
        if (first) countUp(b);
      }).observe(b, { childList: true, characterData: true, subtree: true });
    });

    /* 3. calendar (videos.html): the week card, its sentence, its reminder buttons */
    watchReveal($("weekCard"), function (el) { enter(el, { y: 6 }); });
    var weekText = $("weekText");
    if (weekText) {
      var lastWeek = weekText.innerHTML;
      new MutationObserver(function () {
        if (weekText.innerHTML !== lastWeek) { var first = !lastWeek; lastWeek = weekText.innerHTML; if (!first) swap(weekText); }
      }).observe(weekText, { childList: true, characterData: true, subtree: true });
    }
    watchChildren($("free"), function (nodes) { enter(nodes, { stagger: 0.03, y: 4 }); });
  }

  window.Motion = { enter: enter, pop: pop, swap: swap, lenis: function () { return lenis; }, enabled: can };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();

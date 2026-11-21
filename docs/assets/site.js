/* Deep32 site: tabs, copy buttons and the step navigator. Plain JavaScript, no libraries, no network.
   Every page works without it: tabs show all panels, code is selectable, the navigator is a list of links.
   Copyright (C) 2026 Jeffrey A. Cummings. Apache-2.0. */
(function () {
  'use strict';
  var doc = document;
  doc.documentElement.classList.add('js');

  /* ---- Tabs: one choice of operating system for the whole page ---- */
  var groups = [];
  function selectOs(os, focus) {
    groups.forEach(function (g) {
      var has = g.panels.some(function (p) { return p.os === os; });
      if (!has) { return; }
      g.panels.forEach(function (p) {
        var on = p.os === os;
        p.tab.setAttribute('aria-selected', on ? 'true' : 'false');
        p.tab.tabIndex = on ? 0 : -1;
        p.el.hidden = !on;
        if (on && focus === g) { p.tab.focus(); }
      });
    });
  }
  doc.querySelectorAll('.tabs').forEach(function (box, gi) {
    var panels = [];
    var list = doc.createElement('div');
    list.className = 'tablist';
    list.setAttribute('role', 'tablist');
    box.querySelectorAll(':scope > .tab-panel').forEach(function (el, pi) {
      var label = el.querySelector('.tab-label');
      var os = el.getAttribute('data-os');
      var id = 'tab-' + gi + '-' + pi;
      var tab = doc.createElement('button');
      tab.type = 'button';
      tab.id = id;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', id + '-panel');
      tab.textContent = label ? label.textContent : os;
      el.id = id + '-panel';
      el.setAttribute('role', 'tabpanel');
      el.setAttribute('aria-labelledby', id);
      list.appendChild(tab);
      panels.push({ os: os, tab: tab, el: el });
    });
    if (panels.length < 2) { return; }
    var g = { panels: panels };
    groups.push(g);
    box.insertBefore(list, box.firstChild);
    box.classList.add('ready');
    panels.forEach(function (p, i) {
      p.tab.addEventListener('click', function () { selectOs(p.os); });
      p.tab.addEventListener('keydown', function (e) {
        var k = e.key, n = panels.length, j = -1;
        if (k === 'ArrowRight') { j = (i + 1) % n; } else if (k === 'ArrowLeft') { j = (i - 1 + n) % n; }
        else if (k === 'Home') { j = 0; } else if (k === 'End') { j = n - 1; }
        if (j >= 0) { e.preventDefault(); selectOs(panels[j].os, g); }
      });
    });
  });
  if (groups.length) {
    var ua = (navigator.userAgent || '');
    var first = /Windows/i.test(ua) ? 'windows' : 'linux';
    selectOs(first);
    groups.forEach(function (g) {   // a group without that choice shows its first panel
      if (!g.panels.some(function (p) { return p.tab.getAttribute('aria-selected') === 'true'; })) {
        g.panels.forEach(function (p, i) { p.tab.setAttribute('aria-selected', i === 0 ? 'true' : 'false'); p.tab.tabIndex = i === 0 ? 0 : -1; p.el.hidden = i !== 0; });
      }
    });
  }

  /* ---- Copy buttons on code blocks ---- */
  function copyText(text, done) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { fallback(text, done); });
    } else { fallback(text, done); }
  }
  function fallback(text, done) {
    var ta = doc.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    doc.body.appendChild(ta);
    ta.select();
    try { doc.execCommand('copy'); done(); } catch (e) { /* the text stays selectable */ }
    doc.body.removeChild(ta);
  }
  doc.querySelectorAll('.code').forEach(function (box) {
    var code = box.querySelector('pre code');
    if (!code || box.hasAttribute('data-nocopy')) { return; }
    var btn = doc.createElement('button');
    btn.type = 'button';
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.setAttribute('aria-label', 'Copy to clipboard');
    btn.addEventListener('click', function () {
      copyText(code.textContent.replace(/\n$/, ''), function () {
        btn.textContent = 'Copied';
        btn.classList.add('done');
        setTimeout(function () { btn.textContent = 'Copy'; btn.classList.remove('done'); }, 1600);
      });
    });
    box.appendChild(btn);
  });

  /* ---- Step navigator: current step, progress, compact bar on phones ---- */
  var nav = doc.querySelector('.stepnav');
  if (!nav) { return; }
  var links = Array.prototype.slice.call(nav.querySelectorAll('ol a[href^="#"]'));
  var items = links.map(function (a) {
    return { a: a, el: doc.getElementById(a.getAttribute('href').slice(1)) };
  }).filter(function (it) { return it.el; });
  var now = nav.querySelector('.stepnav-now');
  var bar = nav.querySelector('.progress i');
  var toggle = nav.querySelector('.stepnav-toggle');
  var unit = nav.getAttribute('data-unit') || 'Step';
  var current = -1;

  var narrow = window.matchMedia ? window.matchMedia('(max-width: 960px)') : null;
  function headerOffset() {
    // the front panel covers the top only when it is pinned there; along the bottom, or scrolled away, it doesn't
    var h = doc.querySelector('.site-header');
    var off = 0;
    if (h && window.getComputedStyle(h).position !== 'static') {
      var r = h.getBoundingClientRect();
      if (r.top < 2 && r.bottom > 0) { off = r.height; }
    }
    if (narrow && narrow.matches) { off += nav.querySelector('.stepnav-bar').offsetHeight + 20; }   // the bar under the header
    return off + 24;
  }
  function setCurrent(i) {
    if (i === current) { return; }
    current = i;
    items.forEach(function (it, j) {
      if (j === i) { it.a.setAttribute('aria-current', 'step'); } else { it.a.removeAttribute('aria-current'); }
      it.a.classList.toggle('done', j < i);
      it.el.classList.toggle('active', j === i);   // the window being read takes the active frame colour
    });
    var it = items[i];
    if (now && it) {
      var title = it.a.querySelector('.t');
      now.innerHTML = '';
      var b = doc.createElement('b');
      b.textContent = unit + ' ' + (i + 1) + ' of ' + items.length;
      now.appendChild(b);
      now.appendChild(doc.createTextNode(' · ' + (title ? title.textContent : '')));
    }
    if (bar) { bar.style.width = ((i + 1) / items.length * 100).toFixed(1) + '%'; }
  }
  var ticking = false;
  function update() {
    ticking = false;
    var off = headerOffset();
    var i = 0;
    items.forEach(function (it, j) { if (it.el.getBoundingClientRect().top - off <= 8) { i = j; } });
    var atEnd = (window.innerHeight + window.scrollY) >= (doc.documentElement.scrollHeight - 4);
    if (atEnd) { i = items.length - 1; }
    setCurrent(i);
  }
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; window.requestAnimationFrame(update); }
  }, { passive: true });
  window.addEventListener('resize', update);
  update();

  if (toggle) {
    toggle.hidden = false;
    toggle.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    links.forEach(function (a) {
      a.addEventListener('click', function () {
        if (nav.classList.contains('open')) { nav.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); }
      });
    });
    doc.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && nav.classList.contains('open')) {
        nav.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); toggle.focus();
      }
    });
  }
}());

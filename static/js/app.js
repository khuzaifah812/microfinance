/* SFMS front-end: plain JavaScript, no frameworks. No inline handlers (CSP-safe). */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  function csrf() { var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ($('input[name=csrfmiddlewaretoken]') || {}).value || ''; }

  /* ---- theme (cookie) ---- */
  var root = document.documentElement;
  function setTheme(t) {
    root.setAttribute('data-theme', t);
    document.cookie = 'theme=' + t + '; path=/; max-age=31536000; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '');
    $$('[data-theme-toggle] .material-symbols-outlined').forEach(function (i) { i.textContent = t === 'dark' ? 'light_mode' : 'dark_mode'; });
  }
  function currentTheme() { return root.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); }
  $$('[data-theme-toggle]').forEach(function (b) {
    b.querySelector('.material-symbols-outlined').textContent = currentTheme() === 'dark' ? 'light_mode' : 'dark_mode';
    b.addEventListener('click', function () { setTheme(currentTheme() === 'dark' ? 'light' : 'dark'); });
  });

  /* ---- drawer ---- */
  var mb = $('#menu-btn');
  if (mb) {
    mb.addEventListener('click', function () { document.body.classList.toggle('drawer'); });
    var sc = $('.scrim'); if (sc) sc.addEventListener('click', function () { document.body.classList.remove('drawer'); });
  }

  /* ---- active nav item (longest matching prefix) ---- */
  var best = null, bl = 0;
  $$('.side nav a').forEach(function (a) {
    var h = a.getAttribute('href');
    if ((location.pathname === h || (h !== '/' && location.pathname.indexOf(h) === 0)) && h.length > bl) { best = a; bl = h.length; }
  });
  if (best) best.classList.add('on');

  /* ---- live clock ---- */
  var clock = $('[data-clock]');
  if (clock) {
    var tick = function () { clock.textContent = new Date().toLocaleTimeString('en-GB', { timeZone: 'Africa/Kampala', hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' EAT'; };
    tick(); setInterval(tick, 1000);
  }

  /* ---- dialogs: confirmation, warning and error boxes ---- */
  var ICONS = { confirm: 'help', danger: 'warning', warning: 'error', error: 'error', info: 'info', success: 'check_circle' };
  var lastFocus = null;
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; }
  function dialog(o) {
    return new Promise(function (resolve) {
      var type = o.type || 'confirm', isConfirm = !!o.cancelText;
      var wrap = el('div', 'modal dlg open'); wrap.setAttribute('role', isConfirm ? 'alertdialog' : 'alertdialog'); wrap.setAttribute('aria-modal', 'true');
      var box = el('div', 'box dlgbox dlg-' + type);
      var ic = el('span', 'material-symbols-outlined dlg-icon', ICONS[type] || 'help'); ic.setAttribute('aria-hidden', 'true');
      var h = el('h2', '', o.title || 'Please confirm'); h.id = 'dlg-t'; wrap.setAttribute('aria-labelledby', 'dlg-t');
      box.appendChild(ic); box.appendChild(h);
      if (o.items && o.items.length > 1) { var ul = el('ul', 'dlg-list'); o.items.forEach(function (t) { ul.appendChild(el('li', '', t)); }); box.appendChild(ul); }
      else { box.appendChild(el('p', 'mute dlg-msg', o.message || (o.items && o.items[0]) || '')); }
      var act = el('div', 'actions dlg-actions');
      var ok = el('button', 'btn' + (type === 'danger' ? ' danger' : ''), o.okText || 'OK'); ok.type = 'button';
      var cancel = null;
      if (isConfirm) { cancel = el('button', 'btn ghost', o.cancelText); cancel.type = 'button'; act.appendChild(cancel); }
      act.appendChild(ok); box.appendChild(act); wrap.appendChild(box);
      lastFocus = document.activeElement; document.body.appendChild(wrap); document.body.classList.add('dlg-open');
      var done = function (v) { document.removeEventListener('keydown', key, true); wrap.remove(); document.body.classList.remove('dlg-open'); if (lastFocus && lastFocus.focus) lastFocus.focus(); resolve(v); };
      var key = function (e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); }
        if (e.key === 'Tab') { var f = [cancel, ok].filter(Boolean), i = f.indexOf(document.activeElement); e.preventDefault(); f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus(); }
      };
      document.addEventListener('keydown', key, true);
      ok.addEventListener('click', function () { done(true); });
      if (cancel) cancel.addEventListener('click', function () { done(false); });
      wrap.addEventListener('mousedown', function (e) { if (e.target === wrap && isConfirm) done(false); });
      (type === 'danger' && cancel ? cancel : ok).focus();      // dangerous actions default to Cancel
    });
  }
  window.SFMS = { dialog: dialog };

  /* server messages: errors and warnings open a box that must be acknowledged; success and info slide in as toasts */
  var problems = [], worst = 'warning';
  $$('#toasts .toast.error, #toasts .toast.warning').forEach(function (t) {
    var m = t.querySelector('span'); if (m) problems.push(m.textContent.trim());
    if (t.classList.contains('error')) worst = 'error'; t.remove();
  });
  if (problems.length) dialog({ type: worst === 'error' ? 'danger' : 'warning', title: worst === 'error' ? 'Action not completed' : 'Please note', items: problems, okText: 'OK, understood' });
  $$('#toasts .toast').forEach(function (t, i) {
    setTimeout(function () { t.classList.add('show'); }, 80 + i * 120);
    setTimeout(function () { dismiss(t); }, 7000 + i * 600);
    var x = t.querySelector('button'); if (x) x.addEventListener('click', function () { dismiss(t); });
  });
  function dismiss(t) { t.classList.remove('show'); setTimeout(function () { t.remove(); }, 350); }

  /* confirmations: any element with data-confirm (forms, buttons, links). Tokens: {field} = form field value, {#id} = element text */
  function fill(msg, form) {
    return msg.replace(/\{(#?[\w-]+)\}/g, function (m, k) {
      var n = k.charAt(0) === '#' ? document.getElementById(k.slice(1)) : (form && form.elements[k]);
      if (!n) return m;
      var v = n.tagName === 'SELECT' ? (n.selectedOptions[0] ? n.selectedOptions[0].text : '') : (n.value !== undefined && n.tagName !== 'SPAN' && n.tagName !== 'B' ? n.value : n.textContent);
      v = String(v).trim(); return /^\d+(\.\d+)?$/.test(v) ? Number(v).toLocaleString('en-US') : v;
    });
  }
  function ask(node, form) {
    return dialog({
      type: node.getAttribute('data-confirm-type') || 'confirm', title: node.getAttribute('data-confirm-title') || 'Please confirm',
      message: fill(node.getAttribute('data-confirm'), form), okText: node.getAttribute('data-confirm-ok') || 'Yes, continue', cancelText: 'Cancel'
    });
  }
  document.addEventListener('submit', function (e) {
    var f = e.target; if (!f.hasAttribute || !f.hasAttribute('data-confirm') || f._confirmed) { if (f) f._confirmed = false; return; }
    e.preventDefault(); e.stopImmediatePropagation();
    var sub = e.submitter;
    ask(f, f).then(function (yes) { if (yes) { f._confirmed = true; if (f.requestSubmit) f.requestSubmit(sub || undefined); else f.submit(); } });
  }, true);
  document.addEventListener('click', function (e) {
    var n = e.target.closest ? e.target.closest('button[data-confirm], a[data-confirm]') : null;
    if (!n || n._confirmed || (n.form && n.form.hasAttribute('data-confirm'))) { if (n) n._confirmed = false; return; }
    e.preventDefault(); e.stopImmediatePropagation();
    ask(n, n.form).then(function (yes) { if (yes) { n._confirmed = true; n.click(); } });
  }, true);
  $$('[data-print]').forEach(function (b) { b.addEventListener('click', function () { window.print(); }); });
  $$('form[data-autosubmit] select, form[data-autosubmit] input[type=date]').forEach(function (s) { s.addEventListener('change', function () { s.form.submit(); }); });
  $$('form[data-once]').forEach(function (f) {   // stop double-posting of money transactions
    f.addEventListener('submit', function (e) { if (e.defaultPrevented) return; $$('button[type=submit]', f).forEach(function (b) { setTimeout(function () { b.disabled = true; }, 0); }); });
  });

  /* ---- idle lock: warn at 9 min, sign out at 10 min ---- */
  var body = document.body, idle = parseInt(body.getAttribute('data-idle') || '0', 10), warn = parseInt(body.getAttribute('data-warn') || '0', 10);
  if (idle && $('#idle-modal')) {
    var modal = $('#idle-modal'), cnt = $('#idle-count'), started = Date.now(), iv = null;
    var reset = function () { started = Date.now(); modal.classList.remove('open'); };
    iv = setInterval(function () {
      var s = (Date.now() - started) / 1000;
      if (s >= idle) { clearInterval(iv); var f = $('#logout-form'); if (f) f.submit(); else location.href = '/login/'; return; }
      if (s >= warn) { modal.classList.add('open'); cnt.textContent = Math.max(0, Math.round(idle - s)) + 's'; }
    }, 1000);
    $('#idle-stay').addEventListener('click', function () {
      fetch('/session/keepalive/', { method: 'POST', headers: { 'X-CSRFToken': csrf() }, credentials: 'same-origin' })
        .then(function (r) { if (r.ok) reset(); else location.href = '/login/'; }).catch(function () { location.href = '/login/'; });
    });
    $('#idle-out').addEventListener('click', function () { $('#logout-form').submit(); });
  }

  /* ---- phone tables: copy each column heading onto its cells so the CSS can show them as cards ---- */
  $$('table.tbl:not(.stack)').forEach(function (tb) {     /* short tables become cards; wide ones with totals keep scrolling */
    var cols = $$('thead th', tb).length;
    if (cols >= 2 && cols <= 5 && !tb.querySelector('tr.sum, tr.grp')) tb.classList.add('stack');
  });
  $$('table.stack').forEach(function (tb) {
    var heads = $$('thead th', tb).map(function (h) { return h.textContent.trim(); });
    $$('tbody tr', tb).forEach(function (r) {
      $$('td', r).forEach(function (td, i) { if (!td.classList.contains('empty') && heads[i]) td.setAttribute('data-label', heads[i]); });
    });
  });

  /* ---- searchable, paginated (20/page) client tables ---- */
  $$('table.js-table').forEach(function (tb) {
    var rows = $$('tbody tr', tb).filter(function (r) { return !r.classList.contains('keep') && !r.querySelector('td.empty'); });
    if (rows.length === 0) return;
    var per = 20, page = 1, q = '';
    var wrap = tb.closest('.tablewrap') || tb.parentNode;
    var bar = document.createElement('div'); bar.className = 'tsearch np';
    bar.innerHTML = '<input class="inp" type="search" placeholder="Search this table..." aria-label="Search table">';
    wrap.parentNode.insertBefore(bar, wrap);
    var pager = document.createElement('div'); pager.className = 'pager np'; wrap.parentNode.insertBefore(pager, wrap.nextSibling);
    function draw() {
      var m = rows.filter(function (r) { return !q || r.textContent.toLowerCase().indexOf(q) > -1; });
      var pages = Math.max(1, Math.ceil(m.length / per)); if (page > pages) page = pages;
      rows.forEach(function (r) { r.style.display = 'none'; });
      m.slice((page - 1) * per, page * per).forEach(function (r) { r.style.display = ''; });
      pager.innerHTML = '<span>' + m.length + ' row' + (m.length === 1 ? '' : 's') + '</span><span class="pg"><button type="button" ' + (page <= 1 ? 'class="off"' : '') + ' data-p="-1">Previous</button><span class="cur">' + page + ' / ' + pages + '</span><button type="button" ' + (page >= pages ? 'class="off"' : '') + ' data-p="1">Next</button></span>';
      pager.style.display = m.length <= per && !q ? 'none' : '';
    }
    bar.firstChild.addEventListener('input', function (e) { q = e.target.value.toLowerCase(); page = 1; draw(); });
    pager.addEventListener('click', function (e) { var p = e.target.getAttribute && e.target.getAttribute('data-p'); if (p) { page += parseInt(p, 10); draw(); } });
    draw();
  });

  /* ---- loan preview (calculator + application) ---- */
  var lp = $('[data-loan-preview]');
  if (lp) {
    var out = $('#preview-out'), t = null;
    var money = function (v) { return Math.round(parseFloat(v)).toLocaleString('en-US'); };
    var run = function () {
      var p = $('[name=product]', lp.form || document), a = $('[name=amount]', lp.form || document), n = $('[name=term]', lp.form || document);
      if (!p || !p.value || !a.value || !n.value) { out.innerHTML = ''; return; }
      fetch('/loans/preview/?product=' + encodeURIComponent(p.value) + '&amount=' + encodeURIComponent(a.value) + '&term=' + encodeURIComponent(n.value), { credentials: 'same-origin' })
        .then(function (r) { return r.json(); }).then(function (d) {
          if (d.error) { out.innerHTML = '<div class="note warn">' + d.error.replace(/</g, '&lt;') + '</div>'; return; }
          var h = '<div class="grid"><div class="kpi"><span class="k">Rate / method</span><b>' + d.rate + '% ' + d.method.toLowerCase() + '</b></div><div class="kpi"><span class="k">Total interest</span><b>UGX ' + money(d.total_interest) + '</b></div><div class="kpi"><span class="k">Total payable</span><b>UGX ' + money(d.total_payable) + '</b></div><div class="kpi"><span class="k">Processing fee</span><b>UGX ' + money(d.fee) + '</b></div></div>';
          h += '<div class="tablewrap"><table class="tbl"><thead><tr><th>#</th><th>Due</th><th class="num">Principal</th><th class="num">Interest</th><th class="num">Instalment</th></tr></thead><tbody>';
          d.rows.forEach(function (r) { h += '<tr><td>' + r.no + '</td><td>' + r.due + '</td><td class="num">' + money(r.principal) + '</td><td class="num">' + money(r.interest) + '</td><td class="num">' + money(r.total) + '</td></tr>'; });
          out.innerHTML = h + '</tbody></table></div>';
        });
    };
    var form = lp.closest('form') || document;
    lp.form = form;
    $$('[name=product],[name=amount],[name=term]', form).forEach(function (el) { el.addEventListener('input', function () { clearTimeout(t); t = setTimeout(run, 350); }); el.addEventListener('change', run); });
    run();
  }

  /* ---- manual journal rows ---- */
  var jt = $('#jrows');
  if (jt) {
    var sum = function () {
      var d = 0, c = 0;
      $$('input[name=debit]', jt).forEach(function (i) { d += parseFloat(i.value) || 0; });
      $$('input[name=credit]', jt).forEach(function (i) { c += parseFloat(i.value) || 0; });
      $('#jdr').textContent = Math.round(d).toLocaleString('en-US'); $('#jcr').textContent = Math.round(c).toLocaleString('en-US');
      var ok = d === c && d > 0; $('#jstat').textContent = ok ? 'Balanced' : 'Out of balance by ' + Math.round(Math.abs(d - c)).toLocaleString('en-US'); $('#jstat').className = 'badge ' + (ok ? 'ok' : 'bad');
    };
    jt.addEventListener('input', sum);
    $('#jadd').addEventListener('click', function () { var tr = $('tbody tr', jt).cloneNode(true); $$('input', tr).forEach(function (i) { i.value = ''; }); $('select', tr).selectedIndex = 0; $('tbody', jt).appendChild(tr); });
    sum();
  }

  /* ---- till close: live count ---- */
  var dn = $$('[data-denom]');
  if (dn.length) {
    var expected = parseFloat($('#till-expected').getAttribute('data-v'));
    var calc = function () {
      var tot = 0; dn.forEach(function (i) { var n = parseInt(i.value, 10) || 0; var v = n * parseInt(i.getAttribute('data-denom'), 10); tot += v; $('#sub-' + i.getAttribute('data-denom')).textContent = v.toLocaleString('en-US'); });
      var diff = tot - expected;
      $('#till-actual').textContent = tot.toLocaleString('en-US');
      var d = $('#till-diff'); d.textContent = (diff > 0 ? '+' : '') + diff.toLocaleString('en-US'); d.className = 'badge ' + (diff === 0 ? 'ok' : diff < 0 ? 'bad' : 'warn');
      var box = $('#reason-box'); box.style.display = diff === 0 ? 'none' : ''; $('#reason').required = diff !== 0;
      $('#diff-label').textContent = diff === 0 ? 'Balanced' : (diff < 0 ? 'Shortage - explain below' : 'Overage - explain below');
    };
    dn.forEach(function (i) { i.addEventListener('input', calc); }); calc();
  }

  /* ---- show / hide password on every password field ---- */
  $$('input[type="password"]').forEach(function (inp) {
    if (inp.parentNode.classList.contains('pwwrap')) return;
    var wrap = document.createElement('span'); wrap.className = 'pwwrap';
    inp.parentNode.insertBefore(wrap, inp); wrap.appendChild(inp);
    var b = document.createElement('button'); b.type = 'button'; b.className = 'pwtoggle';
    b.textContent = 'Show'; b.setAttribute('aria-label', 'Show password'); b.setAttribute('aria-pressed', 'false');
    b.addEventListener('click', function () {
      var show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      b.textContent = show ? 'Hide' : 'Show';
      b.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); b.setAttribute('aria-pressed', show ? 'true' : 'false');
      inp.focus();
    });
    wrap.appendChild(b);
  });
  /* never leave a revealed password on screen when the form is submitted or the tab is hidden */
  var hidePw = function () { $$('.pwwrap input').forEach(function (i) { if (i.type === 'text') { i.type = 'password'; var t = i.parentNode.querySelector('.pwtoggle'); if (t) { t.textContent = 'Show'; t.setAttribute('aria-pressed', 'false'); } } }); };
  document.addEventListener('submit', hidePw, true);
  document.addEventListener('visibilitychange', function () { if (document.hidden) hidePw(); });
})();

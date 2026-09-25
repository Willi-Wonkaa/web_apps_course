/* Рендерер интерактивных CJM-деревьев (n-арное дерево, несколько корней).
 *
 * Данные: window.CJM_DATA = { title, subtitle, nodes: [...], edges: [...] }
 *   node  = { id, title, code?, desc?, kind?: 'root'|'stage'|'leaf' }
 *   edge  = { from, to, emotion: 'ok'|'fak'|'wow', label? }
 *
 * Раскладка: сверху вниз по уровням (BFS-глубина от корней), внутри уровня —
 * упорядочивание по поддеревьям, чтобы ветви не пересекались. Точки зафиксированы
 * (пользователь их не двигает); доступны панорамирование (drag), зум (колесо/кнопки),
 * «вписать в экран» и сброс. Всё самодостаточно — без внешних библиотек.
 */
(function () {
  const SVG_NS = 'http://www.w3.org/2000/svg';

  // Геометрия узла и сетки.
  const NODE_W = 230;
  const NODE_H = 74;
  const H_GAP = 34;   // горизонтальный зазор между соседними узлами
  const V_GAP = 130;  // вертикальный шаг между уровнями
  const EMOJI = { ok: '🙂', fak: '😖', wow: '🤩' };
  const EMO_NAME = { ok: 'ok-эмоция', fak: 'fak-эмоция', wow: 'wow-эмоция' };
  const EMO_HINT = {
    ok: 'ожидаемо — всё работает как надо',
    fak: 'неудобно / долго / непонятно',
    wow: 'система дала сверх ожиданий',
  };

  function el(tag, attrs, children) {
    const n = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (children) for (const c of children) n.appendChild(c);
    return n;
  }

  /* ---- Боковая панель с разбором эмоции перехода ---- */
  function ensurePanel() {
    let panel = document.querySelector('.cjm-panel');
    if (panel) return panel;
    panel = document.createElement('div');
    panel.className = 'cjm-panel';
    panel.innerHTML = `
      <div class="cjm-panel-head">
        <div class="cjm-panel-emoji"></div>
        <div class="titles">
          <p class="step"></p>
          <div class="flow"></div>
        </div>
        <button class="cjm-panel-close" title="Закрыть">×</button>
      </div>
      <div class="cjm-panel-body"></div>`;
    document.querySelector('.cjm-stage-wrap').appendChild(panel);
    panel.querySelector('.cjm-panel-close').addEventListener('click', hidePanel);
    return panel;
  }

  function hidePanel() {
    const panel = document.querySelector('.cjm-panel');
    if (panel) panel.classList.remove('open');
    document.querySelectorAll('.edge-group.selected').forEach(n => n.classList.remove('selected'));
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m]));
  }

  function showPanel(edge, from, to) {
    const panel = ensurePanel();
    const emo = edge.emotion || 'ok';
    panel.querySelector('.cjm-panel-emoji').textContent = EMOJI[emo];
    panel.querySelector('.step').textContent = (edge.label ? edge.label : 'Переход');
    panel.querySelector('.flow').textContent = `${from.title}  →  ${to.title}`;

    const body = panel.querySelector('.cjm-panel-body');
    let html = `<span class="emo-tag ${emo}">${EMOJI[emo]} ${EMO_NAME[emo]} · ${EMO_HINT[emo]}</span>`;

    if (edge.why) {
      html += `<div class="sect"><h4>💭 Почему такая эмоция — мысли пользователя</h4><p>«${esc(edge.why)}»</p></div>`;
    }
    if (edge.feel) {
      html += `<div class="sect"><h4>🎯 Что пользователь должен чувствовать</h4><p>${esc(edge.feel)}</p></div>`;
    }
    if (edge.wow) {
      html += `<div class="sect wow-box"><h4>🤩 Что нужно, чтобы стало «вау»</h4><p>${esc(edge.wow)}</p></div>`;
    }
    if (!edge.why && !edge.feel && !edge.wow) {
      html += `<div class="cjm-panel-empty">Для этого перехода детальный разбор не задан.</div>`;
    }
    body.innerHTML = html;
    panel.classList.add('open');
  }

  function wrapText(text, maxChars) {
    const words = String(text).split(/\s+/);
    const lines = [];
    let cur = '';
    for (const w of words) {
      if ((cur + ' ' + w).trim().length > maxChars) {
        if (cur) lines.push(cur);
        cur = w;
      } else {
        cur = (cur + ' ' + w).trim();
      }
    }
    if (cur) lines.push(cur);
    return lines;
  }

  /* ---- Раскладка дерева (Reingold–Tilford, упрощённо) ---- */
  function layout(nodes, edges) {
    const byId = new Map(nodes.map(n => [n.id, Object.assign({}, n, { children: [] })]));
    const hasParent = new Set();
    for (const e of edges) {
      const p = byId.get(e.from), c = byId.get(e.to);
      if (p && c) { p.children.push(c); hasParent.add(c.id); }
    }
    const roots = nodes.filter(n => !hasParent.has(n.id)).map(n => byId.get(n.id));

    // Назначаем глубину (y) обходом от корней. Узел может быть достигнут разными
    // путями — берём максимальную глубину, чтобы ребёнок всегда был ниже родителя.
    let maxDepth = 0;
    const visitedDepth = new Set();
    function setDepth(node, d) {
      node.depth = Math.max(node.depth || 0, d);
      maxDepth = Math.max(maxDepth, node.depth);
      // Защита от повторного спуска в общий узел (несколько родителей/циклы данных).
      const key = node.id + ':' + d;
      if (visitedDepth.has(key)) return;
      visitedDepth.add(key);
      node.children.forEach(ch => setDepth(ch, d + 1));
    }
    roots.forEach(r => setDepth(r, 0));

    // Горизонтальная раскладка: листья занимают слоты слева направо,
    // родитель центрируется над детьми. Общий узел (несколько родителей)
    // размещаем один раз — по первому заходу.
    let cursor = 0;
    const slot = NODE_W + H_GAP;
    const placed = new Set();
    function place(node) {
      if (placed.has(node.id)) return;
      placed.add(node.id);
      const kids = node.children.filter(c => !placed.has(c.id));
      if (kids.length === 0) {
        node.x = cursor * slot;
        cursor += 1;
      } else {
        kids.forEach(place);
        const xs = node.children.map(c => c.x).filter(x => x !== undefined);
        const first = Math.min(...xs);
        const last = Math.max(...xs);
        node.x = (first + last) / 2;
      }
    }
    roots.forEach(r => { place(r); cursor += 1; /* зазор между корнями */ });
    // Узлы, не привязанные ни к одному корню (страховка), — в ряд справа.
    for (const n of byId.values()) { if (n.x === undefined) { n.x = cursor * slot; cursor += 1; } }

    for (const n of byId.values()) {
      n.px = n.x;
      n.py = n.depth * (NODE_H + V_GAP);
    }
    return { byId, roots, maxDepth };
  }

  function render(data) {
    const { byId } = layout(data.nodes, data.edges);

    // Границы холста.
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of byId.values()) {
      minX = Math.min(minX, n.px);
      minY = Math.min(minY, n.py);
      maxX = Math.max(maxX, n.px + NODE_W);
      maxY = Math.max(maxY, n.py + NODE_H);
    }
    const PAD = 80;
    const world = { x: minX - PAD, y: minY - PAD, w: (maxX - minX) + PAD * 2, h: (maxY - minY) + PAD * 2 };

    const svg = document.getElementById('stage');
    svg.setAttribute('viewBox', `${world.x} ${world.y} ${world.w} ${world.h}`);

    const gEdges = el('g', { class: 'edges' });
    const gNodes = el('g', { class: 'nodes' });
    svg.appendChild(gEdges);
    svg.appendChild(gNodes);

    // Рёбра + бейджи эмоций. Каждое ребро кликабельно — открывает панель с разбором.
    const hasDetail = e => e && (e.why || e.feel || e.wow);
    for (const e of data.edges) {
      const p = byId.get(e.from), c = byId.get(e.to);
      if (!p || !c) continue;
      const x1 = p.px + NODE_W / 2, y1 = p.py + NODE_H;
      const x2 = c.px + NODE_W / 2, y2 = c.py;
      const my = (y1 + y2) / 2;
      const d = `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`;
      const cls = `edge-emotion-${e.emotion || 'ok'}`;
      const g = el('g', { class: 'edge-group ' + cls });
      // Данные ребра храним прямо на группе — их достанет обработчик клика (pointerup)
      // после хит-теста; сам click здесь не вешаем, т.к. SVG перехватывает pointer capture.
      g.__edge = { e, p, c };
      g.appendChild(el('path', { class: 'edge ' + cls, d }));
      // Широкая невидимая линия-хитбокс, чтобы по ребру было легко попасть.
      const hit = el('path', { class: 'edge-hit', d });
      g.appendChild(hit);

      // Бейдж с эмодзи-эмоцией на середине ребра.
      const bx = (x1 + x2) / 2, by = my;
      const emoji = EMOJI[e.emotion] || '🙂';
      const labelText = e.label ? String(e.label) : '';
      const badgeW = labelText ? Math.min(200, 26 + labelText.length * 5.6) : 24;
      const badge = el('g', { class: 'edge-badge-hit' });
      badge.appendChild(el('rect', { class: 'edge-badge-bg', x: bx - badgeW / 2, y: by - 11, width: badgeW, height: 22, rx: 9 }));
      const t = el('text', { class: 'edge-badge-emoji', x: bx - badgeW / 2 + 12, y: by + 4, 'text-anchor': 'middle' });
      t.textContent = emoji;
      badge.appendChild(t);
      if (labelText) {
        const lt = el('text', { class: 'edge-label', x: bx - badgeW / 2 + 24, y: by + 3.5 });
        lt.setAttribute('fill', '#fff');
        lt.textContent = labelText;
        badge.appendChild(lt);
      }
      g.appendChild(badge);
      gEdges.appendChild(g);
    }

    // Узлы.
    for (const n of byId.values()) {
      const kind = n.kind || 'leaf';
      const g = el('g', { class: `node-card node-${kind}`, transform: `translate(${n.px} ${n.py})` });
      g.appendChild(el('rect', { class: 'node-rect', x: 0, y: 0, width: NODE_W, height: NODE_H, rx: 10 }));

      let ty = 19;
      if (n.code) {
        const code = el('text', { class: 'node-code', x: 14, y: ty });
        code.textContent = n.code;
        g.appendChild(code);
        ty += 17;
      } else { ty += 2; }

      const titleLines = wrapText(n.title, 30);
      for (let i = 0; i < titleLines.length && i < 2; i++) {
        const tt = el('text', { class: 'node-title', x: 14, y: ty });
        tt.textContent = titleLines[i];
        g.appendChild(tt);
        ty += 16;
      }
      if (n.desc) {
        const descLines = wrapText(n.desc, 36);
        for (let i = 0; i < descLines.length && i < 2; i++) {
          const dt = el('text', { class: 'node-desc', x: 14, y: ty });
          dt.textContent = descLines[i];
          g.appendChild(dt);
          ty += 13;
        }
      }
      gNodes.appendChild(g);
    }

    // Обработчик клика по ребру (без drag) — открывает/закрывает панель.
    // Хит-тест ведём по цепочке родителей от цели события до .edge-group.
    function handleTap(target) {
      let node = target;
      while (node && node !== svg) {
        if (node.classList && node.classList.contains('edge-group') && node.__edge) {
          const { e, p, c } = node.__edge;
          document.querySelectorAll('.edge-group.selected').forEach(n => n.classList.remove('selected'));
          node.classList.add('selected');
          showPanel(e, p, c);
          return;
        }
        node = node.parentNode;
      }
      // Тап по пустому месту — закрываем панель.
      document.querySelectorAll('.edge-group.selected').forEach(n => n.classList.remove('selected'));
      hidePanel();
    }

    setupInteractions(svg, world, handleTap);
    // Титул/подзаголовок.
    if (data.title) {
      const h = document.querySelector('.cjm-topbar h1');
      if (h) h.textContent = data.title;
    }
    if (data.subtitle) {
      const s = document.querySelector('.cjm-topbar .sub');
      if (s) s.textContent = data.subtitle;
    }
  }

  /* ---- Панорамирование и зум через viewBox ---- */
  function setupInteractions(svg, world, onTap) {
    let vb = { x: world.x, y: world.y, w: world.w, h: world.h };
    const fit = { ...vb };

    function apply() { svg.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`); }

    function clientToWorld(cx, cy) {
      const r = svg.getBoundingClientRect();
      return {
        x: vb.x + (cx - r.left) / r.width * vb.w,
        y: vb.y + (cy - r.top) / r.height * vb.h,
      };
    }

    function zoomAt(cx, cy, factor) {
      const before = clientToWorld(cx, cy);
      vb.w *= factor; vb.h *= factor;
      // Ограничим зум.
      const minW = fit.w * 0.15, maxW = fit.w * 3.5;
      if (vb.w < minW) { const k = minW / vb.w; vb.w *= k; vb.h *= k; }
      if (vb.w > maxW) { const k = maxW / vb.w; vb.w *= k; vb.h *= k; }
      const after = clientToWorld(cx, cy);
      vb.x += before.x - after.x;
      vb.y += before.y - after.y;
      apply();
    }

    svg.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      zoomAt(ev.clientX, ev.clientY, ev.deltaY > 0 ? 1.12 : 1 / 1.12);
    }, { passive: false });

    // Различаем «клик» и «перетаскивание»: запоминаем стартовую точку и цель,
    // считаем это кликом, только если указатель почти не сдвинулся (порог 5px).
    const DRAG_THRESHOLD = 5;
    let dragging = false, last = null, downAt = null, downTarget = null, moved = false;
    svg.addEventListener('pointerdown', (ev) => {
      dragging = true; moved = false;
      last = { x: ev.clientX, y: ev.clientY };
      downAt = { x: ev.clientX, y: ev.clientY };
      downTarget = ev.target;
      svg.classList.add('grabbing'); svg.setPointerCapture(ev.pointerId);
    });
    svg.addEventListener('pointermove', (ev) => {
      if (!dragging) return;
      if (!moved && downAt) {
        const dist = Math.hypot(ev.clientX - downAt.x, ev.clientY - downAt.y);
        if (dist > DRAG_THRESHOLD) moved = true;
      }
      const r = svg.getBoundingClientRect();
      const dx = (ev.clientX - last.x) / r.width * vb.w;
      const dy = (ev.clientY - last.y) / r.height * vb.h;
      vb.x -= dx; vb.y -= dy;
      last = { x: ev.clientX, y: ev.clientY };
      apply();
    });
    function endDrag(ev) {
      const wasDragging = dragging;
      dragging = false; svg.classList.remove('grabbing');
      // Не двигали указатель — это тап: определяем цель под курсором и открываем панель.
      if (wasDragging && !moved && onTap) {
        // downTarget точнее (событие могло всплыть на svg из-за pointer capture),
        // но подстрахуемся elementFromPoint по позиции отпускания.
        let tgt = downTarget;
        if (ev && (tgt === svg || !tgt) && typeof document.elementFromPoint === 'function') {
          const el2 = document.elementFromPoint(ev.clientX, ev.clientY);
          if (el2) tgt = el2;
        }
        onTap(tgt);
      }
      downTarget = null; downAt = null; moved = false;
    }
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', () => { dragging = false; svg.classList.remove('grabbing'); });

    function centerOf() { const r = svg.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }
    document.getElementById('zoom-in').onclick = () => { const c = centerOf(); zoomAt(c.x, c.y, 1 / 1.25); };
    document.getElementById('zoom-out').onclick = () => { const c = centerOf(); zoomAt(c.x, c.y, 1.25); };
    document.getElementById('zoom-fit').onclick = () => { vb = { ...fit }; apply(); };

    // Клавиатура: +/- зум, стрелки — панорамирование, 0 — вписать.
    window.addEventListener('keydown', (ev) => {
      const c = centerOf();
      if (ev.key === '+' || ev.key === '=') zoomAt(c.x, c.y, 1 / 1.2);
      else if (ev.key === '-') zoomAt(c.x, c.y, 1.2);
      else if (ev.key === '0') { vb = { ...fit }; apply(); }
      else if (ev.key === 'ArrowLeft') { vb.x -= vb.w * 0.08; apply(); }
      else if (ev.key === 'ArrowRight') { vb.x += vb.w * 0.08; apply(); }
      else if (ev.key === 'ArrowUp') { vb.y -= vb.h * 0.08; apply(); }
      else if (ev.key === 'ArrowDown') { vb.y += vb.h * 0.08; apply(); }
    });

    apply();
  }

  window.CJM = { render };
  document.addEventListener('DOMContentLoaded', () => {
    if (window.CJM_DATA) render(window.CJM_DATA);
  });
})();

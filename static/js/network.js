/* Network page: entities and countries as a force-directed graph.
 *
 * Data from /api/network (nodes with report counts, links with the number of
 * reports two nodes share). The layout is a small force simulation run here:
 * nodes repel, links pull, and everything drifts to the centre. Drag a node
 * to pin it, drag the background to pan, Ctrl + scroll to zoom.
 */
(function () {
    'use strict';
    var root = document.getElementById('net');
    if (!root) { return; }
    var SVG = 'http://www.w3.org/2000/svg';
    var TYPE_COLOURS = {
        country: '#e67e22', organization: '#2980b9', individual: '#8e44ad', policy: '#16a085',
        event: '#c0392b', infrastructure: '#7f8c8d', platform: '#95a5a6'
    };
    var svg = document.getElementById('net-svg');
    var panel = document.getElementById('net-panel');
    var tooltip = document.getElementById('net-tooltip');
    var status = document.getElementById('net-status');
    var params = new URLSearchParams(window.location.search);
    var state = {days: +(params.get('days') || 30), min: +(params.get('min') || 2), hidden: {},
                 selected: params.get('focus'), hover: null};
    var data = null, nodes = [], links = [], byId = {}, view = [0, 0, 1000, 640];
    var layer, linkLayer, nodeLayer, frame = null, alpha = 0;

    function el(name, attrs, parent) {
        var node = document.createElementNS(SVG, name);
        Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
        if (parent) { parent.appendChild(node); }
        return node;
    }
    function html(text) {
        return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
        });
    }
    function radius(n) { return 4 + 2.2 * Math.sqrt(n.reports); }
    function entityUrl(n) {
        return n.slug ? '/country/' + n.slug : '/entity/' + encodeURIComponent(n.id);
    }

    function load() {
        status.hidden = false;
        status.textContent = 'Loading…';
        fetch(root.dataset.api + '?days=' + state.days)
            .then(function (r) { return r.json(); })
            .then(function (body) {
                if (body.error) { status.textContent = 'The database could not be read: ' + body.error; return; }
                data = body;
                var top = body.links.reduce(function (m, l) { return Math.max(m, l.weight); }, 1);
                var range = document.getElementById('net-min');
                range.max = Math.max(2, Math.min(20, top));
                if (state.min > +range.max) { state.min = +range.max; }
                range.value = state.min;
                build(true);
            })
            .catch(function () { status.textContent = 'The network could not be loaded.'; });
    }

    // Nodes and links that pass the filters; positions kept for nodes seen before
    function build(fresh) {
        var old = {};
        nodes.forEach(function (n) { old[n.id] = n; });
        var visible = {};
        links = data.links.filter(function (l) { return l.weight >= state.min; });
        links.forEach(function (l) { visible[l.source] = 1; visible[l.target] = 1; });
        nodes = data.nodes.filter(function (n) { return visible[n.id] && !state.hidden[n.type]; }).map(function (n, i) {
            var prior = old[n.id];
            var angle = i * 2.399963;   // golden angle: an even spiral to start from
            return Object.assign({}, n, prior ? {x: prior.x, y: prior.y, fx: prior.fx, fy: prior.fy} : {
                x: 500 + Math.cos(angle) * (40 + 7 * i), y: 320 + Math.sin(angle) * (40 + 7 * i)
            }, {vx: 0, vy: 0});
        });
        byId = {};
        nodes.forEach(function (n) { byId[n.id] = n; });
        links = links.filter(function (l) { return byId[l.source] && byId[l.target]; })
            .map(function (l) { return {source: byId[l.source], target: byId[l.target], weight: l.weight}; });
        nodes.forEach(function (n) { n.degree = 0; n.neighbours = []; });
        links.forEach(function (l) {
            l.source.neighbours.push({node: l.target, weight: l.weight});
            l.target.neighbours.push({node: l.source, weight: l.weight});
        });
        nodes.forEach(function (n) { n.neighbours.sort(function (a, b) { return b.weight - a.weight; }); });
        status.hidden = nodes.length > 0;
        if (!nodes.length) { status.textContent = 'Nothing to show with these settings; lower "links in at least".'; }
        draw();
        legend();
        if (state.selected && !byId[state.selected]) { state.selected = null; }
        renderPanel();
        heat(fresh ? 1 : 0.5);
        save();
    }

    function draw() {
        while (svg.firstChild) { svg.removeChild(svg.firstChild); }
        svg.setAttribute('viewBox', view.join(' '));
        layer = el('g', {}, svg);
        linkLayer = el('g', {'class': 'net-links'}, layer);
        nodeLayer = el('g', {'class': 'net-nodes'}, layer);
        var labelled = nodes.slice().sort(function (a, b) { return b.reports - a.reports; }).slice(0, 28)
            .reduce(function (m, n) { m[n.id] = 1; return m; }, {});
        links.forEach(function (l) {
            l.el = el('line', {'class': 'net-link'}, linkLayer);
            l.el.style.strokeWidth = (0.6 + 0.7 * Math.sqrt(l.weight)) + 'px';
        });
        nodes.forEach(function (n) {
            var g = el('g', {'class': 'net-node' + (n.code ? ' covered' : ''), tabindex: 0, role: 'button',
                             'aria-label': n.id + ', ' + n.type + ', in ' + n.reports + ' reports'}, nodeLayer);
            g.dataset.id = n.id;
            el('circle', {r: radius(n), fill: TYPE_COLOURS[n.type] || '#999'}, g);
            var text = el('text', {x: radius(n) + 3, y: 4, 'class': labelled[n.id] ? '' : 'minor'}, g);
            text.textContent = n.id;
            n.el = g;
        });
        paint();
    }

    function paint() {
        links.forEach(function (l) {
            l.el.setAttribute('x1', l.source.x); l.el.setAttribute('y1', l.source.y);
            l.el.setAttribute('x2', l.target.x); l.el.setAttribute('y2', l.target.y);
        });
        nodes.forEach(function (n) { n.el.setAttribute('transform', 'translate(' + n.x + ',' + n.y + ')'); });
        emphasise();
    }

    // Hover or selection: the node, its neighbours and their links stand out
    function emphasise() {
        var focus = state.hover || state.selected;
        var near = {};
        if (focus && byId[focus]) {
            near[focus] = 1;
            byId[focus].neighbours.forEach(function (x) { near[x.node.id] = 1; });
        }
        nodes.forEach(function (n) {
            n.el.classList.toggle('dim', !!focus && !near[n.id]);
            n.el.classList.toggle('near', !!focus && !!near[n.id]);
            n.el.classList.toggle('selected', n.id === state.selected);
        });
        links.forEach(function (l) {
            var on = focus && (l.source.id === focus || l.target.id === focus);
            l.el.classList.toggle('dim', !!focus && !on);
            l.el.classList.toggle('on', !!on);
        });
    }

    // --- the simulation ------------------------------------------------------

    function heat(amount) {
        alpha = Math.max(alpha, amount);
        if (!frame) { frame = window.requestAnimationFrame(tick); }
    }

    function tick() {
        frame = null;
        var i, j, a, b, dx, dy, d2, d, f;
        var cx = 500, cy = 320;
        for (i = 0; i < nodes.length; i++) {
            a = nodes[i];
            for (j = i + 1; j < nodes.length; j++) {
                b = nodes[j];
                dx = b.x - a.x; dy = b.y - a.y;
                d2 = dx * dx + dy * dy || 0.01;
                if (d2 > 250000) { continue; }
                f = 2000 / d2;
                d = Math.sqrt(d2);
                a.vx -= f * dx / d; a.vy -= f * dy / d;
                b.vx += f * dx / d; b.vy += f * dy / d;
            }
        }
        links.forEach(function (l) {
            dx = l.target.x - l.source.x; dy = l.target.y - l.source.y;
            d = Math.sqrt(dx * dx + dy * dy) || 0.01;
            var rest = 60 + 170 / Math.sqrt(l.weight);
            f = (d - rest) * 0.012 * Math.min(1, Math.sqrt(l.weight) / 2);
            l.source.vx += f * dx / d; l.source.vy += f * dy / d;
            l.target.vx -= f * dx / d; l.target.vy -= f * dy / d;
        });
        nodes.forEach(function (n) {
            n.vx += (cx - n.x) * 0.004; n.vy += (cy - n.y) * 0.004;
            if (n.fx != null) { n.x = n.fx; n.y = n.fy; n.vx = n.vy = 0; return; }
            n.vx *= 0.82; n.vy *= 0.82;
            n.x += n.vx * alpha; n.y += n.vy * alpha;
        });
        paint();
        alpha *= 0.985;
        if (alpha > 0.02) {
            frame = window.requestAnimationFrame(tick);
            svg.dataset.settled = '';
        } else {
            if (!userView) { fit(); }
            svg.dataset.settled = '1';   // the layout has stopped moving
        }
    }

    // Frame the graph: the nodes' extent plus a margin, in the svg's shape
    var userView = false;
    function fit() {
        if (!nodes.length) { return; }
        var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        nodes.forEach(function (n) {
            var r = radius(n) + 6;
            x0 = Math.min(x0, n.x - r); y0 = Math.min(y0, n.y - r);
            x1 = Math.max(x1, n.x + r + 60); y1 = Math.max(y1, n.y + r);   // room for labels on the right
        });
        var rect = svg.getBoundingClientRect();
        var aspect = rect.width && rect.height ? rect.width / rect.height : 1000 / 640;
        // Never closer than about 2x, so a small graph isn't blown up
        var w = Math.max(x1 - x0, 500), h = Math.max(y1 - y0, 500 / aspect);
        if (w / h < aspect) { w = h * aspect; } else { h = w / aspect; }
        view = [(x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - h / 2, w, h];
        svg.setAttribute('viewBox', view.join(' '));
    }

    // --- panel, legend, tooltip ------------------------------------------------

    function renderPanel() {
        if (!state.selected || !byId[state.selected]) {
            var top = nodes.slice().sort(function (a, b) { return b.neighbours.length - a.neighbours.length; }).slice(0, 10);
            panel.innerHTML = '<h3>Most connected</h3><p class="text-secondary" style="font-size:0.85rem">' + nodes.length + ' nodes, ' +
                links.length + ' links over ' + state.days + ' days. Click one for detail.</p><ol class="geo-rank">' +
                top.map(function (n) {
                    return '<li><button type="button" data-id="' + html(n.id) + '"><span>' + html(n.id) + '</span>' +
                        '<span class="bar"><span style="width:' + (n.neighbours.length / (top[0].neighbours.length || 1) * 100) +
                        '%;background:' + (TYPE_COLOURS[n.type] || '#999') + '"></span></span><span class="val">' + n.neighbours.length + '</span></button></li>';
                }).join('') + '</ol>';
            return;
        }
        var n = byId[state.selected];
        panel.innerHTML = '<p style="margin:0 0 0.5rem"><button type="button" class="geo-btn" id="net-back">← Overview</button></p>' +
            '<h3>' + html(n.id) + '</h3><p><span class="type-chip type-' + html(n.type) + '">' + html(n.type) + '</span> ' +
            '<span class="text-secondary">in ' + n.reports + ' report' + (n.reports === 1 ? '' : 's') + ' over ' + state.days + ' days</span></p>' +
            '<p><a href="' + entityUrl(n) + '">' + (n.code ? 'Country page' : 'Entity page') + '</a>' +
            (n.code ? ' &middot; <a href="/entity/' + encodeURIComponent(n.id) + '">as mentioned by others</a>' : '') + '</p>' +
            '<h4 class="geo-subhead">Appears with</h4><ol class="geo-rank">' + n.neighbours.slice(0, 15).map(function (x) {
                return '<li><button type="button" data-id="' + html(x.node.id) + '"><span>' + html(x.node.id) + '</span>' +
                    '<span class="bar"><span style="width:' + (x.weight / n.neighbours[0].weight * 100) + '%;background:' +
                    (TYPE_COLOURS[x.node.type] || '#999') + '"></span></span><span class="val">' + x.weight + '</span></button></li>';
            }).join('') + '</ol>';
    }

    function legend() {
        var types = {};
        (data ? data.nodes : []).forEach(function (n) { types[n.type] = (types[n.type] || 0) + 1; });
        document.getElementById('net-legend').innerHTML = Object.keys(types).sort().map(function (t) {
            return '<button type="button" class="geo-filter" data-type="' + t + '" aria-pressed="' + !state.hidden[t] + '">' +
                '<span class="geo-swatch" style="background:' + (TYPE_COLOURS[t] || '#999') + '"></span>' + t + ' ' + types[t] + '</button>';
        }).join('') + '<span class="text-secondary" style="font-size:0.8rem">Click a type to hide or show it. Ringed: the countries the agent reports on.</span>';
    }

    function showTooltip(n, event) {
        var box = svg.parentNode.getBoundingClientRect();
        tooltip.innerHTML = '<strong>' + html(n.id) + '</strong><div class="muted">' + html(n.type) + ', in ' + n.reports + ' reports</div>' +
            (n.neighbours.length ? '<div>With ' + n.neighbours.slice(0, 3).map(function (x) { return html(x.node.id) + ' (' + x.weight + ')'; }).join(', ') + '</div>' : '');
        tooltip.style.display = 'block';
        tooltip.style.left = Math.min(event.clientX - box.left + 14, box.width - tooltip.offsetWidth - 8) + 'px';
        tooltip.style.top = (event.clientY - box.top + 14) + 'px';
    }

    function select(id) {
        state.selected = id;
        renderPanel();
        emphasise();
        save();
    }

    function save() {
        var next = new URLSearchParams();
        if (state.days !== 30) { next.set('days', state.days); }
        if (state.min !== 2) { next.set('min', state.min); }
        if (state.selected) { next.set('focus', state.selected); }
        var q = next.toString();
        try { window.history.replaceState(null, '', window.location.pathname + (q ? '?' + q : '')); } catch (e) { /* not allowed */ }
    }

    // --- interaction ------------------------------------------------------------

    function point(event) {
        var m = svg.getScreenCTM().inverse();
        var p = svg.createSVGPoint();
        p.x = event.clientX; p.y = event.clientY;
        return p.matrixTransform(m);
    }

    var drag = null, moved = false;
    svg.addEventListener('pointerdown', function (event) {
        var g = event.target.closest('.net-node');
        moved = false;
        var p = point(event);
        if (g) {
            var n = byId[g.dataset.id];
            drag = {node: n, dx: n.x - p.x, dy: n.y - p.y, x: event.clientX, y: event.clientY};
        } else {
            drag = {pan: true, x: event.clientX, y: event.clientY, view: view.slice()};
        }
        svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener('pointermove', function (event) {
        if (drag) {
            // A press that moves less than 3 px is still a click
            if (!moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 3) { return; }
            moved = true;
            if (drag.node) {
                var p = point(event);
                drag.node.fx = drag.node.x = p.x + drag.dx;
                drag.node.fy = drag.node.y = p.y + drag.dy;
                heat(0.3);
            } else {
                userView = true;
                var rect = svg.getBoundingClientRect();
                view = [drag.view[0] - (event.clientX - drag.x) / rect.width * drag.view[2],
                        drag.view[1] - (event.clientY - drag.y) / rect.height * drag.view[3], drag.view[2], drag.view[3]];
                svg.setAttribute('viewBox', view.join(' '));
            }
            return;
        }
        var g = event.target.closest('.net-node');
        var id = g ? g.dataset.id : null;
        if (id !== state.hover) { state.hover = id; emphasise(); }
        if (g) { showTooltip(byId[id], event); } else { tooltip.style.display = 'none'; }
    });
    // The pointer is captured while pressed, so the release's target is the
    // svg: the node pressed is remembered from pointerdown instead
    svg.addEventListener('pointerup', function () {
        if (drag && !moved) { select(drag.node ? drag.node.id : null); }
        drag = null;
    });
    svg.addEventListener('pointerleave', function () { state.hover = null; tooltip.style.display = 'none'; emphasise(); });
    svg.addEventListener('dblclick', function (event) {
        var g = event.target.closest('.net-node');
        if (g) { var n = byId[g.dataset.id]; n.fx = n.fy = null; heat(0.3); }   // un-pin
    });
    svg.addEventListener('wheel', function (event) {
        if (!(event.ctrlKey || event.metaKey)) { return; }
        event.preventDefault();
        userView = true;
        var p = point(event), factor = event.deltaY < 0 ? 0.8 : 1.25;
        view = [p.x - (p.x - view[0]) * factor, p.y - (p.y - view[1]) * factor, view[2] * factor, view[3] * factor];
        svg.setAttribute('viewBox', view.join(' '));
    }, {passive: false});
    svg.addEventListener('keydown', function (event) {
        var g = event.target.closest('.net-node');
        if (g && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select(g.dataset.id); }
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && state.selected && !event.target.matches('input')) { select(null); }
    });

    panel.addEventListener('click', function (event) {
        var b = event.target.closest('button[data-id]');
        if (b) { select(b.dataset.id); return; }
        if (event.target.id === 'net-back') { select(null); }
    });
    document.getElementById('net-legend').addEventListener('click', function (event) {
        var b = event.target.closest('button[data-type]');
        if (!b) { return; }
        state.hidden[b.dataset.type] = !state.hidden[b.dataset.type];
        build(false);
    });
    var days = document.getElementById('net-days');
    if ([7, 30, 90, 180].indexOf(state.days) < 0) { state.days = 30; }
    days.value = state.days;
    days.addEventListener('change', function () { state.days = +days.value; load(); });
    var min = document.getElementById('net-min');
    var minLabel = document.getElementById('net-min-label');
    function minText() { minLabel.textContent = state.min + ' report' + (state.min === 1 ? '' : 's'); }
    minText();
    min.addEventListener('input', function () { state.min = +min.value; minText(); build(false); });
    document.getElementById('net-reset').addEventListener('click', function () {
        view = [0, 0, 1000, 640];
        userView = false;
        nodes.forEach(function (n) { n.fx = n.fy = null; });
        nodes = [];
        build(true);
    });
    document.getElementById('net-find').addEventListener('input', function (event) {
        var text = event.target.value.trim().toLowerCase();
        if (!text) { return; }
        var hit = nodes.filter(function (n) { return n.id.toLowerCase().indexOf(text) === 0; })[0] ||
                  nodes.filter(function (n) { return n.id.toLowerCase().indexOf(text) >= 0; })[0];
        if (hit) { select(hit.id); }
    });

    load();
})();

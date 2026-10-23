/* Geographic page: a world map of country risk and signals.
 *
 * Draws an SVG map from /static/geo/world.json (outlines projected in
 * advance by tools/build_world_map.py) and colours the countries the agent
 * covers from /api/geo. Everything after the first two requests happens in
 * the browser: the day slider, the metric, the signal window, zoom and pan.
 * A country's top signals are fetched when it is opened.
 *
 * State lives in the URL (?day=&metric=&window=&country=) so a view can be
 * shared or bookmarked.
 */
(function () {
    'use strict';

    var root = document.getElementById('geo');
    if (!root) { return; }
    var SVG = 'http://www.w3.org/2000/svg';
    var DAYS = 60;
    var CATEGORY_COLOURS = {
        geopolitics: '#8e44ad', macro: '#16a085', technology: '#2980b9',
        supply_chain: '#d35400', security: '#c0392b', governance: '#7f8c8d'
    };
    var CATEGORY_NAMES = {
        geopolitics: 'Geopolitics', macro: 'Macro', technology: 'Technology',
        supply_chain: 'Supply chain', security: 'Security', governance: 'Governance'
    };
    // Map-unit boxes (x, y, width, height) on the 1000-wide Equal Earth map
    var REGIONS = {
        world: null,
        americas: [110, 10, 330, 470],
        europe: [440, 15, 190, 120],
        mideast: [560, 85, 150, 115],
        asia: [640, 30, 360, 360],
        africa: [430, 140, 260, 240]
    };
    // Rings that would sit on top of a neighbour's are nudged (map units)
    var GLYPH_NUDGE = { IRL: [-7, 2], GBR: [2, -4], KOR: [-3, 4], JPN: [7, 2], ARE: [6, 6] };
    var RISK_STOPS = [[0, '#fff5c2'], [35, '#fdd176'], [50, '#f99a3e'], [65, '#e3502b'], [80, '#a50f15']];
    // The neutral and low ends follow the theme (set from CSS in themeStops)
    var CHANGE_STOPS = [[-15, '#2166ac'], [-5, '#92c5de'], [0, '#ececec'], [5, '#f4a582'], [15, '#b2182b']];
    var SIGNAL_STOPS = [[0, '#eef3fa'], [0.35, '#9ecae1'], [0.7, '#3182bd'], [1, '#08306b']];
    var STALE_DAYS = 7;   // a carried-forward score older than this is hatched

    var params = new URLSearchParams(window.location.search);
    var state = {
        metric: ['risk', 'change', 'signals'].indexOf(params.get('metric')) >= 0 ? params.get('metric') : 'risk',
        window: [1, 3, 7, 14].indexOf(+params.get('window')) >= 0 ? +params.get('window') : 7,
        glyphs: params.get('rings') !== '0',
        day: null,
        selected: params.get('country'),
        hover: null
    };

    var mapBox = document.getElementById('geo-map');
    var tooltip = document.getElementById('geo-tooltip');
    var panel = document.getElementById('geo-panel');
    var legend = document.getElementById('geo-legend');
    var slider = document.getElementById('geo-day');
    var dayLabel = document.getElementById('geo-day-label');
    var playButton = document.getElementById('geo-play');
    var hint = document.getElementById('geo-hint');

    var world, data, svg, paths = {}, glyphs = {}, view, full, signalMax = 1;
    var signalCache = {};

    // --- small helpers -------------------------------------------------------

    function el(name, attrs, parent) {
        var node = document.createElementNS(SVG, name);
        Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
        if (parent) { parent.appendChild(node); }
        return node;
    }
    function html(text) {
        return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
        });
    }
    function parseDay(text) { var p = text.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
    function formatDay(text, long) {
        return parseDay(text).toLocaleDateString('en-AU', long
            ? {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'}
            : {weekday: 'short', day: 'numeric', month: 'short'});
    }
    function mix(a, b, t) {
        var pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
        var r = Math.round(((pa >> 16) & 255) * (1 - t) + ((pb >> 16) & 255) * t);
        var g = Math.round(((pa >> 8) & 255) * (1 - t) + ((pb >> 8) & 255) * t);
        var bl = Math.round((pa & 255) * (1 - t) + (pb & 255) * t);
        return '#' + ((1 << 24) | (r << 16) | (g << 8) | bl).toString(16).slice(1);
    }
    function scale(stops, value) {
        if (value <= stops[0][0]) { return stops[0][1]; }
        for (var i = 1; i < stops.length; i++) {
            if (value <= stops[i][0]) {
                var t = (value - stops[i - 1][0]) / (stops[i][0] - stops[i - 1][0]);
                return mix(stops[i - 1][1], stops[i][1], t);
            }
        }
        return stops[stops.length - 1][1];
    }
    function gradient(stops, lo, hi) {
        return 'linear-gradient(to right, ' + stops.map(function (s) {
            return s[1] + ' ' + Math.round((s[0] - lo) / (hi - lo) * 100) + '%';
        }).join(', ') + ')';
    }

    // --- figures for a country on a day ----------------------------------------

    function latestRisk(country, index) {
        for (var i = index; i >= 0; i--) {
            if (country.risk[i] != null) { return {value: country.risk[i], index: i, age: index - i}; }
        }
        return null;
    }

    function figures(code, index) {
        var country = data.countries[code];
        var risk = latestRisk(country, index);
        var before = index >= 7 ? latestRisk(country, index - 7) : null;
        var counts = data.categories.map(function () { return 0; });
        var negative = 0, positive = 0;
        for (var i = Math.max(0, index - state.window + 1); i <= index; i++) {
            country.signals[i].forEach(function (n, c) { counts[c] += n; });
            negative += country.negative[i];
            positive += country.positive[i];
        }
        var report = null;
        for (var j = index; j >= 0 && !report; j--) { report = country.reports[j]; }
        return {
            code: code,
            name: country.name,
            slug: country.slug,
            risk: risk,
            change: risk && before ? risk.value - before.value : null,
            counts: counts,
            total: counts.reduce(function (a, b) { return a + b; }, 0),
            negative: negative,
            positive: positive,
            report: report,
            todays: country.reports[index]
        };
    }

    function metricValue(f) {
        if (state.metric === 'risk') { return f.risk ? f.risk.value : null; }
        if (state.metric === 'change') { return f.change; }
        return f.total;
    }

    function colour(f) {
        var value = metricValue(f);
        if (state.metric === 'signals') { return scale(SIGNAL_STOPS, value / signalMax); }
        if (value == null) { return 'url(#geo-nodata)'; }
        if (state.metric === 'change') { return scale(CHANGE_STOPS, value); }
        if (f.risk.age > STALE_DAYS) { return 'url(#geo-stale-' + f.code + ')'; }
        return scale(RISK_STOPS, value);
    }

    // The largest signal count any country reaches with this window, so the
    // colours keep their meaning while the slider moves
    function computeSignalMax() {
        signalMax = 1;
        Object.keys(data.countries).forEach(function (code) {
            for (var i = 0; i < data.days.length; i++) {
                signalMax = Math.max(signalMax, figures(code, i).total);
            }
        });
    }

    // --- drawing -------------------------------------------------------------

    function build() {
        full = [0, 0, world.width, world.height];
        view = full.slice();
        svg = el('svg', {viewBox: full.join(' '), role: 'group', 'aria-label': 'World map'});
        var defs = el('defs', {}, svg);
        var hatch = el('pattern', {id: 'geo-nodata', width: 4, height: 4, patternUnits: 'userSpaceOnUse',
                                   patternTransform: 'rotate(45)'}, defs);
        el('rect', {width: 4, height: 4, fill: 'var(--geo-land)'}, hatch);
        el('line', {x1: 0, y1: 0, x2: 0, y2: 4, stroke: 'var(--text-secondary)', 'stroke-width': 1, opacity: 0.5}, hatch);
        el('rect', {x: -500, y: -500, width: world.width + 1000, height: world.height + 1000, fill: 'transparent'}, svg);
        var land = el('g', {'class': 'geo-land'}, svg);
        var rings = el('g', {'class': 'geo-glyphs'}, svg);

        world.countries.forEach(function (country) {
            var covered = Object.prototype.hasOwnProperty.call(data.countries, country.id);
            var path = el('path', {d: country.d}, land);
            if (!covered) {
                el('title', {}, path).textContent = country.name;
                return;
            }
            path.setAttribute('class', 'covered');
            path.setAttribute('tabindex', '0');
            path.setAttribute('role', 'button');
            path.dataset.code = country.id;
            paths[country.id] = path;
            // A hatched copy of the risk colour for scores carried forward
            var stale = el('pattern', {id: 'geo-stale-' + country.id, width: 5, height: 5,
                                       patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)'}, defs);
            stale._fill = el('rect', {width: 5, height: 5}, stale);
            el('line', {x1: 0, y1: 0, x2: 0, y2: 5, stroke: 'var(--bg-secondary)', 'stroke-width': 1.6, opacity: 0.7}, stale);
            paths[country.id]._stale = stale._fill;

            var nudge = GLYPH_NUDGE[country.id] || [0, 0];
            var g = el('g', {'class': 'glyph'}, rings);
            g.dataset.code = country.id;
            g._at = [country.c[0] + nudge[0], country.c[1] + nudge[1]];
            g._pulse = el('circle', {'class': 'pulse', r: 6}, g);
            g._ring = el('g', {'class': 'ring', transform: 'rotate(-90)'}, g);
            g._arcs = data.categories.map(function (name) {
                return el('circle', {r: 6, pathLength: 100, stroke: CATEGORY_COLOURS[name] || '#999',
                                     'stroke-width': 3.2, 'stroke-dasharray': '0 100'}, g._ring);
            });
            g._edge = el('circle', {'class': 'edge', r: 8}, g);
            g._core = el('circle', {'class': 'core', r: 2.2}, g);
            glyphs[country.id] = g;
        });
        // Glyphs are drawn above every country, so add them after the land
        svg.appendChild(rings);
        mapBox.insertBefore(svg, mapBox.firstChild);
        document.getElementById('geo-loading').remove();
    }

    function themeStops() {
        var css = getComputedStyle(document.documentElement);
        CHANGE_STOPS[2][1] = (css.getPropertyValue('--geo-neutral').trim() || '#ececec');
        SIGNAL_STOPS[0][1] = (css.getPropertyValue('--geo-low').trim() || '#eef3fa');
    }

    function render() {
        themeStops();
        var index = state.day;
        var day = data.days[index];
        slider.value = index;
        dayLabel.textContent = formatDay(day, true);
        Object.keys(paths).forEach(function (code) {
            var f = figures(code, index);
            var path = paths[code];
            path._stale.setAttribute('fill', f.risk ? scale(RISK_STOPS, f.risk.value) : 'var(--geo-land)');
            path.style.fill = colour(f);
            path.setAttribute('aria-label', label(f));
            path.classList.toggle('selected', code === state.selected);

            var g = glyphs[code];
            var radius = f.total ? 3.5 + 1.6 * Math.sqrt(f.total) : 2.6;
            var offset = 0;
            g._arcs.forEach(function (arc, c) {
                var share = f.total ? f.counts[c] / f.total * 100 : 0;
                arc.setAttribute('r', radius);
                arc.setAttribute('stroke-width', Math.max(3, radius * 0.5));
                arc.setAttribute('stroke-dasharray', share + ' ' + (100 - share));
                arc.setAttribute('stroke-dashoffset', -offset);
                offset += share;
            });
            g._pulse.setAttribute('r', radius);
            var half = Math.max(3, radius * 0.5) / 2;
            g._core.setAttribute('r', Math.max(1, radius - half));
            g._edge.setAttribute('r', radius + half);
            g.classList.toggle('rising', f.change != null && f.change >= 5);
            g.classList.toggle('selected', code === state.selected);
            g.style.opacity = f.total ? 1 : 0.55;
        });
        placeGlyphs();
        svg.querySelector('.geo-glyphs').classList.toggle('hidden', !state.glyphs);
        renderLegend();
        renderPanel();
        if (state.hover) { showTooltip(state.hover.code, state.hover.x, state.hover.y); }
        saveState();
    }

    // Rings shrink less than the map as it zooms, so they stay readable
    // without covering a zoomed-in country
    function placeGlyphs() {
        var factor = 1 / Math.sqrt(full[2] / view[2]);
        Object.keys(glyphs).forEach(function (code) {
            var g = glyphs[code];
            g.setAttribute('transform', 'translate(' + g._at[0] + ' ' + g._at[1] + ') scale(' + factor + ')');
        });
    }

    function label(f) {
        var parts = [f.name];
        parts.push(f.risk ? 'risk ' + f.risk.value + (f.risk.age ? ' (' + f.risk.age + ' days old)' : '') : 'no risk score');
        if (f.change != null) { parts.push('change ' + (f.change > 0 ? '+' : '') + f.change); }
        parts.push(f.total + ' signals');
        return parts.join(', ');
    }

    function renderLegend() {
        var parts = [];
        if (state.metric === 'risk') {
            parts.push('<span class="geo-scale">Risk 0 <span class="bar" style="background:' + gradient(RISK_STOPS, 0, 80) + '"></span> 80+</span>');
            parts.push('<span><span class="geo-swatch" style="background: repeating-linear-gradient(45deg, #f99a3e 0 3px, #fff 3px 5px)"></span>score over ' + STALE_DAYS + ' days old</span>');
        } else if (state.metric === 'change') {
            parts.push('<span class="geo-scale">Fell 15 <span class="bar" style="background:' + gradient(CHANGE_STOPS, -15, 15) + '"></span> Rose 15</span>');
        } else {
            parts.push('<span class="geo-scale">0 <span class="bar" style="background:' + gradient(SIGNAL_STOPS, 0, 1) + '"></span> ' + signalMax + ' signals</span>');
        }
        if (state.metric !== 'signals') {
            parts.push('<span><span class="geo-swatch" style="background: repeating-linear-gradient(45deg, var(--geo-land) 0 2px, var(--text-secondary) 2px 3px)"></span>no score</span>');
        }
        if (state.glyphs) {
            parts.push('<span>Rings: signals over ' + windowText() + ' by category ' + data.categories.map(function (c) {
                return '<span class="geo-swatch" style="background:' + CATEGORY_COLOURS[c] + '" title="' + CATEGORY_NAMES[c] + '"></span>';
            }).join('') + '; pulsing: risk up 5+ in a week</span>');
        }
        parts.push('<span>Ctrl + scroll or +/− to zoom, drag to move, [ and ] to step days. Outlines: Natural Earth</span>');
        legend.innerHTML = parts.join('');
    }

    function windowText() {
        return state.window === 1 ? 'the day' : state.window + ' days';
    }

    // --- tooltip -------------------------------------------------------------

    function categoryBar(f) {
        if (!f.total) { return '<div class="geo-mini"></div>'; }
        return '<div class="geo-mini">' + data.categories.map(function (c, i) {
            return f.counts[i] ? '<span style="width:' + (f.counts[i] / f.total * 100) + '%;background:' + CATEGORY_COLOURS[c] + '"></span>' : '';
        }).join('') + '</div>';
    }

    function showTooltip(code, x, y) {
        var f = figures(code, state.day);
        var risk = f.risk
            ? '<div>Risk <strong>' + f.risk.value + '</strong>' +
              (f.change != null ? ' <span class="muted">(' + (f.change > 0 ? '+' : '') + f.change + ' in a week)</span>' : '') +
              (f.risk.age ? '<div class="muted">score from ' + formatDay(data.days[f.risk.index]) + '</div>' : '') + '</div>'
            : '<div class="muted">No risk score yet</div>';
        tooltip.innerHTML = '<strong>' + html(f.name) + '</strong>' + risk +
            '<div>' + f.total + ' signal' + (f.total === 1 ? '' : 's') + ' over ' + windowText() + '</div>' + categoryBar(f) +
            '<div class="muted">' + (f.todays ? 'Report on this day' : f.report ? 'Last report ' + formatDay(f.report.date) : 'No report yet') + '</div>';
        tooltip.style.display = 'block';
        var box = mapBox.getBoundingClientRect();
        var left = Math.min(x - box.left + 14, box.width - tooltip.offsetWidth - 8);
        var top = y - box.top + 14;
        if (top + tooltip.offsetHeight > box.height - 4) { top = y - box.top - tooltip.offsetHeight - 10; }
        tooltip.style.left = Math.max(4, left) + 'px';
        tooltip.style.top = Math.max(4, top) + 'px';
    }

    function hideTooltip() {
        state.hover = null;
        tooltip.style.display = 'none';
        highlight(null);
    }

    function highlight(code) {
        Object.keys(paths).forEach(function (c) { paths[c].classList.toggle('hover', c === code); });
        panel.querySelectorAll('[data-code]').forEach(function (b) { b.classList.toggle('hover', b.dataset.code === code); });
    }

    // --- side panel: the ranking, or one country -----------------------------

    function renderPanel() {
        if (state.selected && data.countries[state.selected]) { renderDetail(state.selected); }
        else { renderRanking(); }
    }

    function renderRanking() {
        var index = state.day;
        var rows = Object.keys(data.countries).map(function (code) { return figures(code, index); });
        rows.sort(function (a, b) {
            var va = metricValue(a), vb = metricValue(b);
            if (va == null && vb == null) { return a.name.localeCompare(b.name); }
            if (va == null) { return 1; }
            if (vb == null) { return -1; }
            return vb - va || a.name.localeCompare(b.name);
        });
        var title = {risk: 'Highest risk', change: 'Biggest risers', signals: 'Most signals'}[state.metric];
        var spread = state.metric === 'change'
            ? Math.max(5, Math.max.apply(null, rows.map(function (r) { return Math.abs(r.change || 0); })))
            : state.metric === 'signals' ? Math.max(1, rows[0].total) : 100;
        panel.innerHTML = '<h3>' + title + '</h3><p class="text-secondary" style="font-size:0.85rem">' +
            formatDay(data.days[index], true) + (state.metric === 'signals' ? ', over ' + windowText() : '') +
            '. Click a country for its signals.</p><ol class="geo-rank">' + rows.map(function (f) {
                var value = metricValue(f);
                var width = value == null ? 0 : Math.abs(value) / spread * 100;
                var shown = value == null ? '–' : state.metric === 'change' && value > 0 ? '+' + value : value;
                var stale = state.metric === 'risk' && f.risk && f.risk.age > STALE_DAYS ? ' <span class="stale">' + f.risk.age + 'd old</span>' : '';
                var fill = value == null ? 'transparent' : state.metric === 'signals' ? scale(SIGNAL_STOPS, value / signalMax)
                    : state.metric === 'change' ? scale(CHANGE_STOPS, value) : scale(RISK_STOPS, value);
                return '<li><button type="button" data-code="' + f.code + '"><span>' + html(f.name) + stale + '</span>' +
                    '<span class="bar"><span style="width:' + width + '%;background:' + fill + '"></span></span>' +
                    '<span class="val">' + shown + '</span></button></li>';
            }).join('') + '</ol>';
    }

    function sparkline(code) {
        var country = data.countries[code];
        var w = 300, h = 60, n = data.days.length;
        var scored = country.risk.filter(function (v) { return v != null; });
        if (!scored.length) { return '<p class="text-secondary" style="font-size:0.85rem">No scores in this period.</p>'; }
        // Scale to the scores' own range, at least 10 points tall
        var lo = Math.min.apply(null, scored), hi = Math.max.apply(null, scored);
        if (hi - lo < 10) { lo = Math.max(0, (lo + hi) / 2 - 5); hi = lo + 10; }
        var x = function (i) { return i / (n - 1) * w; };
        var y = function (v) { return h - 4 - (v - lo) / (hi - lo) * (h - 8); };
        // Days without a score break the line
        var segments = [], current = [];
        country.risk.forEach(function (v, i) {
            if (v == null) {
                if (current.length > 1) { segments.push(current.join(' ')); }
                current = [];
                return;
            }
            current.push(x(i).toFixed(1) + ',' + y(v).toFixed(1));
        });
        if (current.length > 1) { segments.push(current.join(' ')); }
        var dots = country.risk.map(function (v, i) {
            return v == null ? '' : '<circle class="dot" cx="' + x(i).toFixed(1) + '" cy="' + y(v).toFixed(1) + '" r="1.8"></circle>';
        }).join('');
        var now = x(state.day).toFixed(1);
        return '<div class="geo-spark-wrap"><div class="geo-spark-axis" aria-hidden="true"><span>' + Math.round(hi) + '</span><span>' + Math.round(lo) + '</span></div>' +
            '<svg class="geo-spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" role="img" aria-label="Risk from ' + Math.round(lo) + ' to ' + Math.round(hi) + ' over ' + DAYS + ' days">' +
            '<line class="now" x1="' + now + '" x2="' + now + '" y1="0" y2="' + h + '"></line>' +
            segments.map(function (s) { return '<polyline class="line" points="' + s + '"></polyline>'; }).join('') + dots + '</svg></div>';
    }

    function renderDetail(code) {
        var f = figures(code, state.day);
        var day = data.days[state.day];
        var changeChip = f.change == null ? '' : '<span class="chip ' + (f.change > 0 ? 'up' : f.change < 0 ? 'down' : 'flat') + '">' +
            (f.change > 0 ? '▲ +' : f.change < 0 ? '▼ ' : '') + f.change + ' in a week</span>';
        var cats = data.categories.map(function (c, i) {
            return f.counts[i] ? '<li><span class="geo-swatch" style="background:' + CATEGORY_COLOURS[c] + '"></span>' +
                CATEGORY_NAMES[c] + ' ' + f.counts[i] + '</li>' : '';
        }).join('');
        var links = [];
        if (f.todays) { links.push('<li><a href="/reports/' + f.todays.id + '">Report of ' + formatDay(f.todays.date) + '</a></li>'); }
        else if (f.report) { links.push('<li><a href="/reports/' + f.report.id + '">Latest report, ' + formatDay(f.report.date) + '</a></li>'); }
        if (f.todays || f.report) {
            links.push('<li><a href="/reports/' + (f.todays || f.report).id + '/changes">What changed since the one before</a></li>');
        }
        links.push('<li><a href="/country/' + f.slug + '">' + html(f.name) + ' page: risk over 90 days</a></li>');
        links.push('<li><a href="/reports?series=mika-' + f.slug + '">All ' + html(f.name) + ' reports</a></li>');

        panel.innerHTML = '<div class="geo-detail">' +
            '<p style="margin:0 0 0.5rem"><button type="button" class="geo-btn" id="geo-back">← All countries</button> ' +
            '<button type="button" class="geo-btn" id="geo-focus">Zoom to ' + html(f.name) + '</button></p>' +
            '<h3>' + html(f.name) + '</h3>' +
            '<div><span class="big">' + (f.risk ? f.risk.value : '–') + '</span> <span class="text-secondary">risk</span> ' + changeChip + '</div>' +
            '<div class="text-secondary" style="font-size:0.8rem">' + (f.risk ? (f.risk.age ? 'Score from ' + formatDay(data.days[f.risk.index]) + ', the latest by ' : 'On ') + formatDay(day) : 'No risk score in the last ' + DAYS + ' days') + '</div>' +
            '<section><h4>Risk, last ' + DAYS + ' days</h4>' + sparkline(code) + '</section>' +
            '<section><h4>' + f.total + ' signal' + (f.total === 1 ? '' : 's') + ' over ' + windowText() + '</h4>' +
            '<div class="geo-cats">' + data.categories.map(function (c, i) {
                return f.counts[i] ? '<span style="width:' + (f.counts[i] / f.total * 100) + '%;background:' + CATEGORY_COLOURS[c] + '"></span>' : '';
            }).join('') + '</div><ul class="geo-cat-list">' + cats + '</ul>' +
            (f.negative || f.positive ? '<p class="text-secondary" style="font-size:0.8rem">' + f.negative + ' negative, ' + f.positive + ' positive</p>' : '') +
            '</section>' +
            '<section><h4>Top signals</h4><ul class="geo-signals" id="geo-signal-list"><li class="text-secondary">Loading…</li></ul></section>' +
            '<section><h4>Reports</h4><ul class="geo-links">' + links.join('') + '</ul></section>' +
            '</div>';
        loadSignals(code, day);
    }

    function loadSignals(code, day) {
        var key = code + '|' + day + '|' + state.window;
        var fill = function (signals) {
            var list = document.getElementById('geo-signal-list');
            if (!list || state.selected !== code) { return; }
            list.innerHTML = signals.length ? signals.map(function (s) {
                return '<li><span class="geo-swatch" style="background:' + (CATEGORY_COLOURS[s.category] || '#999') + '"></span>' +
                    '<span title="' + html(s.summary) + '">' + html(s.title) + '</span>' +
                    '<div class="meta">' + (CATEGORY_NAMES[s.category] || s.category) + ' · ' + formatDay(s.date) +
                    (s.impact != null ? ' · impact ' + s.impact : '') + (s.bias ? ' · ' + s.bias : '') + '</div></li>';
            }).join('') : '<li class="text-secondary">No signals in this window.</li>';
        };
        if (signalCache[key]) { fill(signalCache[key]); return; }
        fetch('/api/geo/' + code + '/signals?end=' + day + '&days=' + state.window)
            .then(function (r) { return r.json(); })
            .then(function (body) { signalCache[key] = body.signals || []; fill(signalCache[key]); })
            .catch(function () { fill([]); });
    }

    function select(code) {
        state.selected = code;
        hideTooltip();
        render();
        if (code && window.matchMedia('(max-width: 1000px)').matches) {
            panel.scrollIntoView({behavior: 'smooth', block: 'start'});
        }
    }

    // --- zoom and pan --------------------------------------------------------

    // Width / height of the map on screen; the view keeps this shape
    function aspect() {
        var rect = svg.getBoundingClientRect();
        return rect.width && rect.height ? rect.width / rect.height : full[2] / full[3];
    }

    // The whole map, or on a narrow screen its full height around Europe and Asia
    function worldView() {
        var a = aspect();
        if (a >= full[2] / full[3] * 0.9) { return full.slice(); }
        var w = full[3] * a;
        return [620 - w / 2, 0, w, full[3]];
    }

    function setView(box, animate) {
        var a = aspect();
        var w = Math.min(full[2], Math.max(60, box[2]));
        var h = w / a;
        var slackX = w * 0.05, slackY = h * 0.05;
        var x = box[0] + (box[2] - w) / 2, y = box[1] + (box[3] - h) / 2;
        x = Math.min(Math.max(x, Math.min(0, full[2] - w) - slackX), Math.max(0, full[2] - w) + slackX);
        y = Math.min(Math.max(y, Math.min(0, full[3] - h) - slackY), Math.max(0, full[3] - h) + slackY);
        var target = [x, y, w, h];
        if (!animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            view = target;
            svg.setAttribute('viewBox', view.join(' '));
            placeGlyphs();
            return;
        }
        var from = view.slice(), start = null;
        function step(time) {
            if (start === null) { start = time; }
            var t = Math.min(1, (time - start) / 450);
            var ease = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
            view = from.map(function (v, i) { return v + (target[i] - v) * ease; });
            svg.setAttribute('viewBox', view.join(' '));
            placeGlyphs();
            if (t < 1) { window.requestAnimationFrame(step); }
        }
        window.requestAnimationFrame(step);
    }

    // Fit a box (map units) into the map's shape, centred
    function fit(box) {
        var a = aspect();
        var w = box[2], h = box[3];
        if (w / h < a) { w = h * a; } else { h = w / a; }
        return [box[0] - (w - box[2]) / 2, box[1] - (h - box[3]) / 2, w, h];
    }

    function zoomBy(factor, cx, cy) {
        if (cx == null) { cx = view[0] + view[2] / 2; cy = view[1] + view[3] / 2; }
        var w = view[2] / factor, h = view[3] / factor;
        setView([cx - (cx - view[0]) / factor, cy - (cy - view[1]) / factor, w, h], false);
    }

    function mapPoint(clientX, clientY) {
        var rect = svg.getBoundingClientRect();
        return [view[0] + (clientX - rect.left) / rect.width * view[2],
                view[1] + (clientY - rect.top) / rect.height * view[3]];
    }

    function showHint(text) {
        hint.textContent = text;
        hint.classList.add('show');
        clearTimeout(showHint.timer);
        showHint.timer = setTimeout(function () { hint.classList.remove('show'); }, 1400);
    }

    function wirePanZoom() {
        var pointers = {}, drag = null, moved = false, pinch = null;

        svg.addEventListener('wheel', function (event) {
            if (!(event.ctrlKey || event.metaKey)) { showHint('Ctrl + scroll to zoom, drag to move'); return; }
            event.preventDefault();
            var p = mapPoint(event.clientX, event.clientY);
            zoomBy(event.deltaY < 0 ? 1.25 : 0.8, p[0], p[1]);
        }, {passive: false});

        svg.addEventListener('dblclick', function (event) {
            var p = mapPoint(event.clientX, event.clientY);
            zoomBy(event.shiftKey ? 0.5 : 2, p[0], p[1]);
        });

        svg.addEventListener('pointerdown', function (event) {
            pointers[event.pointerId] = [event.clientX, event.clientY];
            var ids = Object.keys(pointers);
            if (ids.length === 1) {
                drag = {x: event.clientX, y: event.clientY, view: view.slice()};
                moved = false;
            } else if (ids.length === 2) {
                var a = pointers[ids[0]], b = pointers[ids[1]];
                pinch = {d: Math.hypot(a[0] - b[0], a[1] - b[1]), view: view.slice(),
                         centre: mapPoint((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)};
                drag = null;
            }
        });
        svg.addEventListener('pointermove', function (event) {
            if (pointers[event.pointerId]) { pointers[event.pointerId] = [event.clientX, event.clientY]; }
            var ids = Object.keys(pointers);
            if (pinch && ids.length === 2) {
                var a = pointers[ids[0]], b = pointers[ids[1]];
                var factor = Math.hypot(a[0] - b[0], a[1] - b[1]) / pinch.d;
                var w = pinch.view[2] / factor, h = pinch.view[3] / factor;
                var c = pinch.centre;
                view = pinch.view.slice();
                setView([c[0] - (c[0] - pinch.view[0]) / factor, c[1] - (c[1] - pinch.view[1]) / factor, w, h], false);
                moved = true;
                return;
            }
            if (drag) {
                var dx = event.clientX - drag.x, dy = event.clientY - drag.y;
                if (!moved && Math.hypot(dx, dy) < 4) { return; }
                if (!moved) { svg.setPointerCapture(event.pointerId); }
                moved = true;
                svg.classList.add('dragging');
                hideTooltip();
                var rect = svg.getBoundingClientRect();
                view = [drag.view[0] - dx / rect.width * drag.view[2], drag.view[1] - dy / rect.height * drag.view[3],
                        drag.view[2], drag.view[3]];
                setView(view, false);
            }
        });
        function release(event) {
            delete pointers[event.pointerId];
            if (Object.keys(pointers).length < 2) { pinch = null; }
            if (!Object.keys(pointers).length) { drag = null; svg.classList.remove('dragging'); }
        }
        svg.addEventListener('pointerup', release);
        svg.addEventListener('pointercancel', release);
        // A drag ends with a click; don't let it open a country
        svg.addEventListener('click', function (event) {
            if (moved) { moved = false; event.stopPropagation(); event.preventDefault(); return; }
            var target = event.target.closest('[data-code]');
            select(target ? target.dataset.code : null);
        }, true);

        document.getElementById('geo-zoom-in').addEventListener('click', function () { zoomBy(1.6); });
        document.getElementById('geo-zoom-out').addEventListener('click', function () { zoomBy(1 / 1.6); });
        root.querySelectorAll('[data-region]').forEach(function (button) {
            button.addEventListener('click', function () {
                var box = REGIONS[button.dataset.region];
                setView(box ? fit(box) : worldView(), true);
            });
        });
    }

    function zoomToCountry(code) {
        var box = paths[code].getBBox();
        // Russia and the USA reach across the map; their main body is what matters
        var w = Math.max(box.width, 40) * 1.6, h = Math.max(box.height, 30) * 1.6;
        var g = glyphs[code]._at;
        var cx = box.width > 300 ? g[0] : box.x + box.width / 2;
        var cy = box.width > 300 ? g[1] : box.y + box.height / 2;
        if (box.width > 300) { w = 420; h = 220; }
        setView(fit([cx - w / 2, cy - h / 2, w, h]), true);
    }

    // --- controls ------------------------------------------------------------

    var playing = null;
    function stop() {
        clearInterval(playing);
        playing = null;
        playButton.setAttribute('aria-pressed', 'false');
        playButton.innerHTML = '&#9654;';
        playButton.setAttribute('aria-label', 'Play through the days');
    }
    function play() {
        if (state.day >= data.days.length - 1) { state.day = Math.max(0, data.days.length - 30); render(); }
        playButton.setAttribute('aria-pressed', 'true');
        playButton.innerHTML = '&#10074;&#10074;';
        playButton.setAttribute('aria-label', 'Pause');
        playing = setInterval(function () {
            if (state.day >= data.days.length - 1) { stop(); return; }
            state.day += 1;
            render();
        }, 650);
    }

    function wireControls() {
        root.querySelectorAll('input[name=metric]').forEach(function (input) {
            input.checked = input.value === state.metric;
            input.addEventListener('change', function () { state.metric = input.value; render(); });
        });
        var windowSelect = document.getElementById('geo-window');
        windowSelect.value = String(state.window);
        windowSelect.addEventListener('change', function () {
            state.window = +windowSelect.value;
            computeSignalMax();
            render();
        });
        var glyphBox = document.getElementById('geo-glyphs');
        glyphBox.checked = state.glyphs;
        glyphBox.addEventListener('change', function () { state.glyphs = glyphBox.checked; render(); });
        slider.max = data.days.length - 1;
        slider.addEventListener('input', function () { stop(); state.day = +slider.value; render(); });
        playButton.addEventListener('click', function () { if (playing) { stop(); } else { play(); } });
        document.getElementById('geo-today').addEventListener('click', function () {
            stop();
            state.day = data.days.length - 1;
            render();
        });

        panel.addEventListener('click', function (event) {
            var row = event.target.closest('button[data-code]');
            if (row) { select(row.dataset.code); return; }
            if (event.target.id === 'geo-back') { select(null); }
            if (event.target.id === 'geo-focus') { zoomToCountry(state.selected); }
        });
        panel.addEventListener('mouseover', function (event) {
            var row = event.target.closest('button[data-code]');
            highlight(row ? row.dataset.code : null);
        });
        panel.addEventListener('mouseleave', function () { highlight(null); });

        svg.addEventListener('pointermove', function (event) {
            if (event.pointerType === 'touch') { return; }
            var target = event.target.closest('[data-code]');
            if (!target) { if (state.hover) { hideTooltip(); } return; }
            state.hover = {code: target.dataset.code, x: event.clientX, y: event.clientY};
            highlight(target.dataset.code);
            showTooltip(target.dataset.code, event.clientX, event.clientY);
        });
        svg.addEventListener('pointerleave', hideTooltip);

        svg.addEventListener('keydown', function (event) {
            var target = event.target.closest('[data-code]');
            if (target && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault();
                select(target.dataset.code);
            }
        });
        svg.addEventListener('focusin', function (event) {
            var target = event.target.closest('[data-code]');
            if (!target) { return; }
            var rect = target.getBoundingClientRect();
            showTooltip(target.dataset.code, rect.left + rect.width / 2, rect.top + rect.height / 2);
        });
        svg.addEventListener('focusout', function () { tooltip.style.display = 'none'; });

        document.addEventListener('keydown', function (event) {
            if (event.target.matches('input, select, textarea')) { return; }
            if (event.key === 'Escape' && state.selected) { select(null); }
            else if (event.key === '+' || event.key === '=') { zoomBy(1.6); }
            else if (event.key === '-') { zoomBy(1 / 1.6); }
            else if (event.key === '[' && state.day > 0) { stop(); state.day -= 1; render(); }
            else if (event.key === ']' && state.day < data.days.length - 1) { stop(); state.day += 1; render(); }
        });
    }

    function saveState() {
        var next = new URLSearchParams();
        if (state.day !== data.days.length - 1) { next.set('day', data.days[state.day]); }
        if (state.metric !== 'risk') { next.set('metric', state.metric); }
        if (state.window !== 7) { next.set('window', state.window); }
        if (!state.glyphs) { next.set('rings', '0'); }
        if (state.selected) { next.set('country', state.selected); }
        var query = next.toString();
        try {
            window.history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
        } catch (e) { /* a sandboxed frame may refuse; the page works without it */ }
    }

    function fail(message) {
        var loading = document.getElementById('geo-loading');
        if (loading) { loading.textContent = message; }
    }

    // Match the sticky panel to the header's real height
    var header = document.querySelector('header');
    function place() { document.documentElement.style.setProperty('--header-height', header.offsetHeight + 'px'); }
    place();
    window.addEventListener('resize', place);

    Promise.all([
        fetch(root.dataset.world).then(function (r) { if (!r.ok) { throw new Error('map'); } return r.json(); }),
        fetch(root.dataset.api + '?days=' + DAYS).then(function (r) { return r.json(); })
    ]).then(function (results) {
        world = results[0];
        data = results[1];
        if (data.error) { fail('The database could not be read: ' + data.error); return; }
        var wanted = data.days.indexOf(params.get('day'));
        state.day = wanted >= 0 ? wanted : data.days.length - 1;
        if (state.selected && !data.countries[state.selected]) { state.selected = null; }
        build();
        computeSignalMax();
        wireControls();
        wirePanZoom();
        setView(worldView(), false);
        render();
        // Recolour when the theme switches
        new MutationObserver(render).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
        // Keep the view's shape when the window changes size
        window.addEventListener('resize', function () {
            setView([view[0], view[1], view[2], view[3]], false);
        });
    }).catch(function () { fail('The map could not be loaded.'); });
})();

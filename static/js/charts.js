/* Charts page: a day slider with play, like the Geographic page's.
 *
 * Moving the slider marks the day on every risk chart (scores after it
 * fade, the heading shows the score as of that day) and redraws the signal
 * grid for the 7 days up to it from /api/charts/signals, fetched once.
 * The page still works without this script: it is the server's view of
 * the latest 7 days, and ?end= picks another.
 */
(function () {
    'use strict';

    var box = document.getElementById('chart-time');
    var riskGrid = document.getElementById('risk-grid');
    if (!box || !riskGrid) { return; }
    var SVG = 'http://www.w3.org/2000/svg';
    var GRID_DAYS = 7;

    var slider = document.getElementById('chart-day');
    var label = document.getElementById('chart-day-label');
    var playButton = document.getElementById('chart-play');

    function parseDay(text) { var p = text.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
    function iso(d) {
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }
    function addDays(d, n) { var c = new Date(d); c.setDate(c.getDate() + n); return c; }
    function show(text, options) { return parseDay(text).toLocaleDateString('en-AU', options); }

    // The days the risk charts' axis covers, first to last
    var first = parseDay(riskGrid.dataset.first), last = parseDay(riskGrid.dataset.last);
    var days = [];
    for (var d = first; d <= last; d = addDays(d, 1)) { days.push(iso(d)); }
    if (!days.length) { return; }
    var span = Math.max(1, days.length - 1);
    var width = +riskGrid.dataset.width, pad = +riskGrid.dataset.pad;

    var charts = Array.prototype.map.call(riskGrid.querySelectorAll('svg[data-scores]'), function (svg) {
        var height = svg.viewBox.baseVal.height;
        var shade = document.createElementNS(SVG, 'rect');
        shade.setAttribute('class', 'future');
        shade.setAttribute('y', 0);
        shade.setAttribute('height', height);
        var cursor = document.createElementNS(SVG, 'line');
        cursor.setAttribute('class', 'cursor');
        cursor.setAttribute('y1', 0);
        cursor.setAttribute('y2', height);
        svg.appendChild(shade);
        svg.appendChild(cursor);
        var chart = svg.closest('.chart');
        return {
            svg: svg,
            scores: JSON.parse(svg.dataset.scores || '[]'),
            dots: Array.prototype.slice.call(svg.querySelectorAll('circle.dot')),
            shade: shade,
            cursor: cursor,
            value: chart.querySelector('.chart-value'),
            original: chart.querySelector('.chart-value') ? chart.querySelector('.chart-value').innerHTML : ''
        };
    });

    var cells = null;   // from /api/charts/signals: category -> [cell per day]
    var cellDays = {};

    var initial = days.indexOf(box.dataset.end);
    var state = {day: initial >= 0 ? initial : days.length - 1};

    function x(index) { return pad + (width - 2 * pad) * index / span; }

    function renderRisk() {
        var day = days[state.day];
        var at = x(state.day);
        charts.forEach(function (chart) {
            chart.cursor.setAttribute('x1', at);
            chart.cursor.setAttribute('x2', at);
            chart.shade.setAttribute('x', at);
            chart.shade.setAttribute('width', Math.max(0, width - at));
            var seen = chart.scores.filter(function (s) { return s[0] <= day; });
            chart.dots.forEach(function (dot, n) {
                var score = chart.scores[n];
                dot.classList.toggle('later', !!score && score[0] > day);
            });
            if (!chart.value) { return; }
            if (state.day === days.length - 1) { chart.value.innerHTML = chart.original; return; }
            if (!seen.length) { chart.value.innerHTML = '<small class="text-secondary">no score yet</small>'; return; }
            var latest = seen[seen.length - 1], before = seen[seen.length - 2];
            var change = before ? latest[1] - before[1] : 0;
            chart.value.innerHTML = latest[1] + (change ? ' <small class="' + (change > 0 ? 'text-danger' : 'text-success') + '">' +
                (change > 0 ? '+' : '') + change + '</small>' : '') +
                (latest[0] !== day ? ' <small class="text-secondary">' + show(latest[0], {day: 'numeric', month: 'short'}) + '</small>' : '');
        });
    }

    function categoryName(c) { var t = c.replace(/_/g, ' '); return t.charAt(0).toUpperCase() + t.slice(1); }

    function renderGrid() {
        if (!cells) { return; }
        var end = state.day;
        var shown = [];
        for (var i = GRID_DAYS - 1; i >= 0; i--) { shown.push(iso(addDays(parseDay(days[end]), -i))); }
        var most = 0, total = 0;
        Object.keys(cells).forEach(function (category) {
            shown.forEach(function (day) {
                var cell = cells[category][cellDays[day]];
                if (cell) { most = Math.max(most, cell[0]); total += cell[0]; }
            });
        });
        document.getElementById('grid-head').innerHTML = '<th>Category</th>' + shown.map(function (day) {
            return '<th class="nowrap">' + show(day, {weekday: 'short'}) + ' ' + String(parseDay(day).getDate()).padStart(2, '0') + '</th>';
        }).join('');
        document.getElementById('grid-body').innerHTML = Object.keys(cells).map(function (category) {
            return '<tr><th>' + categoryName(category) + '</th>' + shown.map(function (day) {
                var cell = cells[category][cellDays[day]];
                if (!cell) { return '<td class="empty">·</td>'; }
                var count = cell[0], scored = cell[2];
                var shade = Math.round((0.15 + 0.85 * count / most) * 100) / 100;
                var impact = scored ? 'average impact ' + Math.round(cell[1] / scored) + (scored < count ? ' (' + scored + ' scored)' : '') : 'no impact scores';
                return '<td class="cell"><span style="background: rgba(52, 152, 219, ' + shade + '); color: ' + (shade > 0.5 ? 'white' : 'inherit') +
                    ';" title="' + count + ' signal' + (count === 1 ? '' : 's') + '; ' + impact + '; ' + cell[3] + ' negative, ' + cell[4] + ' positive">' +
                    count + '</span></td>';
            }).join('') + '</tr>';
        }).join('');
        document.getElementById('grid-range').textContent =
            show(shown[0], {day: '2-digit', month: 'short'}) + ' to ' + show(shown[shown.length - 1], {day: '2-digit', month: 'short', year: 'numeric'});
        document.getElementById('grid').hidden = !total;
        var empty = document.getElementById('grid-empty');
        if (empty) { empty.hidden = !!total; }
    }

    function render() {
        slider.value = state.day;
        label.textContent = show(days[state.day], {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'});
        renderRisk();
        renderGrid();
        var query = new URLSearchParams(window.location.search);
        if (state.day === days.length - 1) { query.delete('end'); } else { query.set('end', days[state.day]); }
        var text = query.toString();
        try { window.history.replaceState(null, '', window.location.pathname + (text ? '?' + text : '')); } catch (e) { /* not allowed here */ }
    }

    var playing = null;
    function stop() {
        clearInterval(playing);
        playing = null;
        playButton.setAttribute('aria-pressed', 'false');
        playButton.innerHTML = '&#9654;';
        playButton.setAttribute('aria-label', 'Play through the days');
    }
    function play() {
        if (state.day >= days.length - 1) { state.day = Math.max(0, days.length - 30); render(); }
        playButton.setAttribute('aria-pressed', 'true');
        playButton.innerHTML = '&#10074;&#10074;';
        playButton.setAttribute('aria-label', 'Pause');
        playing = setInterval(function () {
            if (state.day >= days.length - 1) { stop(); return; }
            state.day += 1;
            render();
        }, 500);
    }

    slider.max = days.length - 1;
    slider.addEventListener('input', function () { stop(); state.day = +slider.value; render(); });
    playButton.addEventListener('click', function () { if (playing) { stop(); } else { play(); } });
    document.getElementById('chart-today').addEventListener('click', function () { stop(); state.day = days.length - 1; render(); });
    document.addEventListener('keydown', function (event) {
        if (event.target.matches('input, select, textarea')) { return; }
        if (event.key === '[' && state.day > 0) { stop(); state.day -= 1; render(); }
        else if (event.key === ']' && state.day < days.length - 1) { stop(); state.day += 1; render(); }
    });

    box.hidden = false;
    render();
    // The grid needs a week before the axis starts, too
    var need = days.length + GRID_DAYS - 1;
    fetch(box.dataset.api + '?end=' + days[days.length - 1] + '&days=' + need)
        .then(function (r) { return r.json(); })
        .then(function (body) {
            if (body.error) { return; }
            body.days.forEach(function (day, n) { cellDays[day] = n; });
            cells = body.cells;
            renderGrid();
        })
        .catch(function () { /* the server's grid stays */ });
})();

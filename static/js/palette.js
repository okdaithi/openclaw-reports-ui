/* Go-to palette: Ctrl+K (or Cmd+K, or "/") from any page.
 *
 * Lists pages, countries, entities and reports matching what is typed
 * (from /api/palette); arrow keys choose, Enter opens, Escape closes.
 * Enter with nothing chosen searches the reports for the text.
 */
(function () {
    'use strict';
    var dialog = document.getElementById('palette');
    if (!dialog || typeof dialog.showModal !== 'function') { return; }
    var input = document.getElementById('palette-input');
    var list = document.getElementById('palette-list');
    var items = [], active = 0, timer = null, sequence = 0;

    function escapeHtml(text) {
        return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
        });
    }

    function open() {
        input.value = '';
        dialog.showModal();
        input.focus();
        load('');
    }

    function load(query) {
        var mine = ++sequence;
        fetch('/api/palette?q=' + encodeURIComponent(query))
            .then(function (r) { return r.json(); })
            .then(function (body) {
                if (mine !== sequence) { return; }
                items = body.items || [];
                active = 0;
                draw();
            })
            .catch(function () { items = []; draw(); });
    }

    function draw() {
        list.innerHTML = items.map(function (item, n) {
            return '<li role="option" id="palette-' + n + '" aria-selected="' + (n === active) + '">' +
                '<a href="' + escapeHtml(item.url) + '" tabindex="-1"><span class="kind">' + escapeHtml(item.kind) + '</span>' +
                '<span class="label">' + escapeHtml(item.label) + '</span>' +
                (item.detail ? '<span class="detail">' + escapeHtml(item.detail) + '</span>' : '') + '</a></li>';
        }).join('');
        if (items.length) {
            input.setAttribute('aria-activedescendant', 'palette-' + active);
            var chosen = document.getElementById('palette-' + active);
            if (chosen) { chosen.scrollIntoView({block: 'nearest'}); }
        } else {
            input.removeAttribute('aria-activedescendant');
        }
    }

    function go() {
        if (items[active]) { window.location.href = items[active].url; return; }
        var text = input.value.trim();
        if (text) { window.location.href = '/reports?q=' + encodeURIComponent(text); }
    }

    document.addEventListener('keydown', function (event) {
        var typing = event.target.matches('input, textarea, select, [contenteditable="true"]');
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
            event.preventDefault();
            if (dialog.open) { dialog.close(); } else { open(); }
        } else if (event.key === '/' && !typing && !dialog.open) {
            event.preventDefault();
            open();
        }
    });

    input.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(function () { load(input.value.trim()); }, 120);
    });
    input.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowDown' && items.length) {
            event.preventDefault();
            active = (active + 1) % items.length;
            draw();
        } else if (event.key === 'ArrowUp' && items.length) {
            event.preventDefault();
            active = (active - 1 + items.length) % items.length;
            draw();
        } else if (event.key === 'Enter') {
            event.preventDefault();
            go();
        }
    });
    list.addEventListener('mousemove', function (event) {
        var li = event.target.closest('li');
        if (!li) { return; }
        var n = +li.id.split('-')[1];
        if (n !== active) { active = n; draw(); }
    });
    // A click on the dim backdrop (the dialog element itself) closes it
    dialog.addEventListener('click', function (event) {
        if (event.target === dialog) { dialog.close(); }
    });

    var opener = document.getElementById('search-open');
    if (opener) { opener.addEventListener('click', open); }

    // The header's search box: Ctrl+K hint; plain Enter still searches reports
    var headerSearch = document.getElementById('header-search');
    if (headerSearch) {
        headerSearch.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowDown') { event.preventDefault(); open(); input.value = headerSearch.value; load(headerSearch.value.trim()); }
        });
    }

    // Menus in the nav: one open at a time; a click elsewhere or Escape closes them
    var menus = Array.prototype.slice.call(document.querySelectorAll('.nav-menu'));
    menus.forEach(function (menu) {
        menu.addEventListener('toggle', function () {
            if (menu.open) { menus.forEach(function (other) { if (other !== menu) { other.open = false; } }); }
        });
    });
    document.addEventListener('click', function (event) {
        menus.forEach(function (menu) { if (menu.open && !menu.contains(event.target)) { menu.open = false; } });
    });
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') { return; }
        menus.forEach(function (menu) {
            if (menu.open) { menu.open = false; menu.querySelector('summary').focus(); }
        });
    });
})();

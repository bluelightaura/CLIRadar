from __future__ import annotations

from html import escape

from .models import Catalog

_SEARCH_SCRIPT = """
  (function () {
    var input = document.getElementById('search');
    if (!input) return;
    var counter = document.getElementById('counter');
    var rows = Array.prototype.slice.call(
      document.querySelectorAll('tbody tr')
    );
    function apply() {
      var query = input.value.trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (row) {
        var hit = !query || row.getAttribute('data-text').indexOf(query) !== -1;
        row.hidden = !hit;
        if (hit) shown += 1;
      });
      counter.textContent = shown + ' из ' + rows.length;
    }
    input.addEventListener('input', apply);
    apply();
  })();
"""


def _command_rows(commands: list[dict[str, object]]) -> str:
    if not commands:
        return '<p class="empty">Команды не найдены.</p>'

    rows = []
    for index, item in enumerate(commands, start=1):
        command = escape(str(item.get("command", "")))
        description = escape(str(item.get("description", ""))) or "—"
        haystack = escape(
            f"{item.get('command', '')} {item.get('description', '')}".lower()
        )
        rows.append(
            f'<tr data-text="{haystack}"><td>{index}</td>'
            f"<td><code>{command}</code></td>"
            f"<td>{description}</td></tr>"
        )
    total = len(rows)
    return (
        '<div class="toolbar">'
        '<input id="search" type="search" autocomplete="off" spellcheck="false"'
        ' placeholder="Поиск по командам и описаниям…">'
        f'<span id="counter">{total} из {total}</span>'
        "</div>"
        '<div class="table-wrap"><table>'
        "<thead><tr><th>#</th><th>Команда</th><th>Описание</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _context_state(item: dict[str, object]) -> str:
    """Say why a context is incomplete instead of just that it is."""
    if item.get("complete"):
        return "полный"
    reasons = []
    mismatched = item.get("derived_mismatched")
    if isinstance(mismatched, list) and mismatched:
        # The loudest reason first: a copied subtree the device disagreed with.
        reasons.append(f"выведенные ветки не подтвердились: {len(mismatched)}")
    if item.get("derived_truncated"):
        reasons.append("копирование упёрлось в лимит")
    parameters = item.get("skipped_parameters")
    if isinstance(parameters, list) and parameters:
        shown = ", ".join(str(token) for token in parameters[:4])
        if len(parameters) > 4:
            shown += f" и ещё {len(parameters) - 4}"
        reasons.append(f"нет примера для {shown}")
    if item.get("skipped_denied"):
        reasons.append(f"запрещено веток: {item['skipped_denied']}")
    if item.get("skipped_depth"):
        reasons.append(f"упёрлось в глубину: {item['skipped_depth']}")
    return "неполный — " + "; ".join(reasons) if reasons else "неполный"


def _context_rows(contexts: list[dict[str, object]]) -> str:
    rows = []
    for item in contexts:
        path = " › ".join(str(step) for step in (item.get("entry_path") or [])) or "корень"
        rows.append(
            f"<tr><td><code>{escape(str(item.get('fingerprint', '')))}</code></td>"
            f"<td>{escape(path)}</td>"
            f"<td>{escape(str(item.get('commands', 0)))}</td>"
            f"<td>{escape(_context_state(item))}</td></tr>"
        )
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>Промпт</th><th>Путь входа</th><th>Команд</th><th>Обход</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _executed_block(executed: list[str]) -> str:
    """Everything the scanner typed with Enter, so a reader can verify it."""
    if not executed:
        return '<p class="notice">Ни одна команда не выполнялась: только контекстная справка.</p>'
    items = "".join(f"<li><code>{escape(command)}</code></li>" for command in executed)
    return (
        '<details class="audit"><summary>Показать все '
        f"{len(executed)} выполненных команд</summary><ol>{items}</ol></details>"
    )


def _firmware_section(device: object) -> str:
    """State which firmware the catalog describes.

    A command surface only means something next to the software that exposed
    it, so this sits above the findings rather than in a footnote.
    """
    if not isinstance(device, dict):
        return ""
    firmware = device.get("firmware")
    if not isinstance(firmware, dict):
        return ""
    results = firmware.get("results")
    if not isinstance(results, list) or not results:
        return ""
    blocks = []
    for item in results:
        if not isinstance(item, dict):
            continue
        command = escape(str(item.get("command", "")))
        if "output" in item and str(item["output"]).strip():
            blocks.append(
                f"<p><code>{command}</code></p><pre>{escape(str(item['output']))}</pre>"
            )
        else:
            reason = escape(str(item.get("error", "пустой ответ")))
            blocks.append(
                f"<p><code>{command}</code> — <em>не удалось получить: {reason}</em></p>"
            )
    if not blocks:
        return ""
    return (
        "<h2>Версия ПО устройства</h2>"
        "<p class='notice'>Снято на устройстве до обхода. Каталог команд "
        "описывает именно эту прошивку.</p>"
        f"<div class='firmware'>{''.join(blocks)}</div>"
    )


def _graph_section(scan: dict[str, object]) -> str:
    contexts = scan.get("contexts")
    if not isinstance(contexts, list) or not contexts:
        return ""
    executed = scan.get("executed_commands")
    executed_list = [str(item) for item in executed] if isinstance(executed, list) else []
    reopens = escape(str(scan.get("channel_reopens", 0)))
    return (
        f"<h2>Контексты CLI <span>{len(contexts)}</span></h2>"
        f"{_context_rows([item for item in contexts if isinstance(item, dict)])}"
        f"<h2>Выполненные команды <span>{len(executed_list)}</span></h2>"
        f"<p class='notice'>Переоткрытий канала: {reopens}. Enter нажимается "
        "только при входе в режимы и при снятии версии ПО.</p>"
        f"{_executed_block(executed_list)}"
    )


def render_html_report(catalog: Catalog) -> str:
    payload = catalog.to_dict()
    mode = str(payload["mode"])
    scan = payload["scan"]
    summary = payload["summary"]
    commands = payload["commands"]
    if (
        not isinstance(scan, dict)
        or not isinstance(summary, dict)
        or not isinstance(commands, list)
    ):
        raise TypeError("catalog serialisation changed shape; the report cannot trust it")

    complete = bool(scan.get("complete"))
    missing = [
        item
        for item in commands
        if isinstance(item, dict) and item.get("comparison_status") == "missing_on_device"
    ]
    not_observed = [
        item
        for item in commands
        if isinstance(item, dict) and item.get("comparison_status") == "not_observed"
    ]

    if mode == "compare":
        # An incomplete walk still proves an absence wherever it did stand:
        # the device listed that node's keywords in full. Those findings are
        # stated outright; only the rest is hedged.
        body = (
            f"<h2>Нет на устройстве <span>{len(missing)}</span></h2>"
            f"{_command_rows(missing)}"
        )
        if not_observed:
            body += (
                f"<h2>Не обнаружены <span>{len(not_observed)}</span></h2>"
                '<p class="warning"><strong>Обход не дошёл до этих ветвей.</strong> '
                "Нельзя утверждать, что перечисленных команд нет на устройстве.</p>"
                f"{_command_rows(not_observed)}"
            )
    else:
        body = (
            '<p class="notice">Режим <code>audit</code> не использует документацию, '
            "поэтому определить отсутствующие команды невозможно.</p>"
        )
    body = _firmware_section(payload.get("device")) + body
    body += _graph_section(scan)

    generated_at = escape(str(payload["generated_at"]))
    device_commands = escape(str(summary.get("device_commands", 0)))
    documentation_commands = escape(str(summary.get("documentation_commands", "—")))
    queries = escape(str(scan.get("queries", 0)))
    completeness = "полный" if complete else "неполный"

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
  <title>CLIRadar — отсутствующие команды</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: system-ui, "Segoe UI", sans-serif;
      --border: #88888844;
      --soft: #88888818;
      --accent: #4470dd;
    }}
    * {{ box-sizing: border-box; }}
    body {{ max-width: 1100px; margin: 0 auto; padding: 1.5rem 1rem 3rem; line-height: 1.5; }}
    header {{
      display: flex; justify-content: space-between; align-items: baseline;
      gap: 1rem; flex-wrap: wrap; padding-bottom: 1rem; margin-bottom: 1.25rem;
      border-bottom: 1px solid var(--border);
    }}
    header h1 {{ margin: 0; font-size: 1.5rem; letter-spacing: .02em; }}
    header p {{ margin: 0; opacity: .8; }}
    .stats {{ display: flex; gap: .75rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
    .stats div {{
      flex: 1 1 10rem; padding: .8rem 1rem; border: 1px solid var(--border);
      border-radius: .75rem; background: var(--soft);
    }}
    .stats strong {{ display: block; font-size: 1.5rem; font-variant-numeric: tabular-nums; }}
    .stats small {{ opacity: .75; }}
    h2 {{ margin: 1.5rem 0 .75rem; font-size: 1.2rem; }}
    h2 span {{
      font-size: .8em; padding: .1rem .55rem; border-radius: 1rem;
      background: var(--soft); border: 1px solid var(--border);
      font-variant-numeric: tabular-nums;
    }}
    .toolbar {{
      display: flex; gap: .75rem; align-items: center; flex-wrap: wrap;
      position: sticky; top: 0; padding: .6rem 0; background: Canvas; z-index: 1;
    }}
    #search {{
      flex: 1 1 16rem; font: inherit; padding: .55rem .9rem;
      border: 1px solid var(--border); border-radius: .6rem;
      background: var(--soft); color: inherit; outline: none;
    }}
    #search:focus {{ border-color: var(--accent); }}
    #counter {{ opacity: .75; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: .75rem; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: .5rem .8rem; text-align: left; border-bottom: 1px solid var(--border); }}
    thead th {{ background: var(--soft); font-weight: 600; }}
    tbody tr:last-child td {{ border-bottom: none; }}
    tbody tr:hover {{ background: var(--soft); }}
    th:first-child, td:first-child {{ width: 3.5rem; opacity: .6; }}
    code {{ overflow-wrap: anywhere; font-size: .95em; }}
    .warning {{
      padding: .9rem 1.1rem; border-left: .3rem solid #d98b00;
      background: #d98b0018; border-radius: 0 .6rem .6rem 0;
    }}
    .notice, .empty {{ padding: .9rem 1.1rem; background: var(--soft); border-radius: .6rem; }}
    .firmware pre {{
      margin: .4rem 0 1rem; padding: .8rem 1rem; overflow-x: auto;
      border: 1px solid var(--border); border-radius: .6rem; background: var(--soft);
    }}
    .audit {{ border: 1px solid var(--border); border-radius: .6rem; padding: .6rem .9rem; }}
    .audit summary {{ cursor: pointer; }}
    .audit ol {{ max-height: 22rem; overflow-y: auto; margin: .6rem 0 0; }}
    footer {{ margin-top: 2rem; opacity: .6; font-size: .85rem; }}
  </style>
</head>
<body>
  <header>
    <h1>CLIRadar</h1>
    <p>Режим: <code>{escape(mode)}</code> · обход: {completeness}</p>
  </header>
  <section class="stats" aria-label="Сводка">
    <div><strong>{device_commands}</strong><small>на устройстве</small></div>
    <div><strong>{documentation_commands}</strong><small>в документации</small></div>
    <div><strong>{queries}</strong><small>запросов CLI</small></div>
  </section>
  <main>{body}</main>
  <footer>Сформировано {generated_at}. Отчёт автономный и не загружает внешние ресурсы.</footer>
  <script>{_SEARCH_SCRIPT}</script>
</body>
</html>
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

import attrs

from edgeapt.constants import COMPONENT
from edgeapt.keyring import SigningKey
from edgeapt.models import LockFile

SUITE_LABELS = {
    "jammy": "jammy (22)",
    "noble": "noble (24)",
}


@attrs.define(kw_only=True, frozen=True)
class InstallPageResult:
    index_html: Path
    public_ascii: Path
    public_keyring: Path


@attrs.define(kw_only=True, frozen=True)
class InstallPageContext:
    profile: str
    suites: tuple[str, ...]
    arches: tuple[str, ...]


def write_install_page(
    *,
    output_dir: Path,
    profile: str,
    lock: LockFile,
    signing_key: SigningKey,
) -> InstallPageResult:
    context = InstallPageContext(
        profile=profile,
        suites=_published_suites(lock),
        arches=_published_arches(lock),
    )
    public_ascii = output_dir / "edgeapt.asc"
    public_keyring = output_dir / "edgeapt.gpg"
    shutil.copy2(signing_key.public_ascii, public_ascii)
    shutil.copy2(signing_key.public_keyring, public_keyring)

    index_html = output_dir / "index.html"
    index_html.write_text(_render_index_html(context), encoding="utf-8")
    return InstallPageResult(
        index_html=index_html,
        public_ascii=public_ascii,
        public_keyring=public_keyring,
    )


def _published_suites(lock: LockFile) -> tuple[str, ...]:
    suites: set[str] = set()
    for source_lock in lock.sources.values():
        for artifact in source_lock.artifacts:
            suites.update(artifact.suites)
    return tuple(sorted(suites))


def _published_arches(lock: LockFile) -> tuple[str, ...]:
    arches: set[str] = set()
    for source_lock in lock.sources.values():
        for artifact in source_lock.artifacts:
            arches.add(artifact.arch)
    return tuple(sorted(arches))


def _render_index_html(context: InstallPageContext) -> str:
    suites = context.suites or ("noble",)
    arches = context.arches or ("amd64",)
    suite_options = "\n".join(
        _render_option(value=suite, selected=index == 0)
        for index, suite in enumerate(suites)
    )
    arch_options = "\n".join(
        _render_option(value=arch, selected=index == 0)
        for index, arch in enumerate(arches)
    )
    escaped_profile = html.escape(context.profile)
    escaped_component = html.escape(COMPONENT)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EdgeAPT Repository</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
      color: #172026;
      background: #f7f8f5;
    }}
    body {{
      margin: 0;
    }}
    main {{
      width: min(960px, calc(100% - 32px));
      margin: 0 auto;
      padding: 40px 0 56px;
    }}
    header {{
      margin-bottom: 28px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 5vw, 3.4rem);
      line-height: 1.05;
      letter-spacing: 0;
    }}
    p {{
      margin: 0 0 16px;
      max-width: 720px;
      color: #43505a;
    }}
    section {{
      padding: 24px 0;
      border-top: 1px solid #d7ddd4;
    }}
    h2 {{
      margin: 0 0 16px;
      font-size: 1.2rem;
      letter-spacing: 0;
    }}
    .controls {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }}
    label {{
      display: grid;
      gap: 6px;
      font-weight: 650;
    }}
    select {{
      width: 100%;
      border: 1px solid #aeb8ad;
      border-radius: 6px;
      background: #fff;
      color: inherit;
      padding: 10px 12px;
      font: inherit;
    }}
    .toggle {{
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 44px;
    }}
    input[type="checkbox"] {{
      width: 18px;
      height: 18px;
      accent-color: #216869;
    }}
    .command {{
      margin-top: 16px;
    }}
    .command-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 8px;
    }}
    .command-title {{
      font-weight: 700;
    }}
    button {{
      border: 1px solid #216869;
      border-radius: 6px;
      background: #216869;
      color: #fff;
      font: inherit;
      font-weight: 700;
      padding: 8px 12px;
      cursor: pointer;
    }}
    pre {{
      margin: 0;
      overflow-x: auto;
      white-space: pre;
      border: 1px solid #202a2f;
      border-radius: 6px;
      background: #10181d;
      color: #e9f2ee;
      padding: 16px;
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      font-size: 0.92rem;
    }}
    .meta {{
      color: #5f6d75;
      font-size: 0.95rem;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>EdgeAPT Repository</h1>
      <p>Install packages from this signed APT repository. The commands below use the current page URL as the repository base URL.</p>
      <p class="meta">Profile: {escaped_profile}</p>
    </header>

    <section>
      <h2>Options</h2>
      <div class="controls">
        <label>
          Ubuntu suite
          <select id="suite">
{suite_options}
          </select>
        </label>
        <label>
          Architecture
          <select id="arch">
{arch_options}
          </select>
        </label>
        <label class="toggle">
          <input id="deb822" type="checkbox" checked>
          Use DEB822 source format
        </label>
      </div>
      <p class="meta">DEB822 is recommended for current Ubuntu releases. Legacy one-line format remains available for older systems or manual setups.</p>
    </section>

    <section>
      <h2>Install</h2>
      <div class="command">
        <div class="command-header">
          <span class="command-title">Setup commands</span>
          <button type="button" data-copy-target="setup">Copy</button>
        </div>
        <pre><code id="setup"></code></pre>
      </div>
      <div class="command">
        <div class="command-header">
          <span class="command-title">Example install</span>
          <button type="button" data-copy-target="example">Copy</button>
        </div>
        <pre><code id="example"></code></pre>
      </div>
    </section>
  </main>

  <script>
    const component = "{escaped_component}";
    const keyringPath = "/etc/apt/keyrings/edgeapt.gpg";
    const sourceName = "edgeapt";
    const baseUrl = new URL(".", window.location.href).href;

    const suiteSelect = document.getElementById("suite");
    const archSelect = document.getElementById("arch");
    const deb822Input = document.getElementById("deb822");
    const setupOutput = document.getElementById("setup");
    const exampleOutput = document.getElementById("example");

    function buildDeb822Source(suite, arch) {{
      return `Types: deb
URIs: ${{baseUrl}}
Suites: ${{suite}}
Components: ${{component}}
Architectures: ${{arch}}
Signed-By: ${{keyringPath}}`;
    }}

    function buildLegacySource(suite, arch) {{
      return `deb [arch=${{arch}} signed-by=${{keyringPath}}] ${{baseUrl}} ${{suite}} ${{component}}`;
    }}

    function shellQuote(value) {{
      return `'${{value.replaceAll("'", "'\\\\''")}}'`;
    }}

    function updateCommands() {{
      const suite = suiteSelect.value;
      const arch = archSelect.value;
      const keyUrl = new URL("edgeapt.gpg", baseUrl).href;

      let setup;
      if (deb822Input.checked) {{
        setup = `sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL ${{shellQuote(keyUrl)}} | sudo tee ${{keyringPath}} >/dev/null

sudo tee /etc/apt/sources.list.d/${{sourceName}}.sources >/dev/null <<'EOF'
${{buildDeb822Source(suite, arch)}}
EOF

sudo apt update`;
      }} else {{
        setup = `sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL ${{shellQuote(keyUrl)}} | sudo tee ${{keyringPath}} >/dev/null

echo ${{shellQuote(buildLegacySource(suite, arch))}} | sudo tee /etc/apt/sources.list.d/${{sourceName}}.list >/dev/null

sudo apt update`;
      }}

      setupOutput.textContent = setup;
      exampleOutput.textContent = `apt-cache search edgeapt
sudo apt install <package-name>`;
    }}

    async function copyCode(targetId, button) {{
      const target = document.getElementById(targetId);
      if (!target) return;
      await navigator.clipboard.writeText(target.textContent);
      const original = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(() => {{
        button.textContent = original;
      }}, 1200);
    }}

    for (const input of [suiteSelect, archSelect, deb822Input]) {{
      input.addEventListener("change", updateCommands);
    }}
    for (const button of document.querySelectorAll("[data-copy-target]")) {{
      button.addEventListener("click", () => copyCode(button.dataset.copyTarget, button));
    }}
    updateCommands();
  </script>
</body>
</html>
"""


def _render_option(*, value: str, selected: bool) -> str:
    escaped = html.escape(value)
    label = html.escape(SUITE_LABELS.get(value, value))
    selected_attr = " selected" if selected else ""
    return f'            <option value="{escaped}"{selected_attr}>{label}</option>'

"use strict";

const { createApp } = Vue;
const PAGE_SIZE = 20;

createApp({
  data() {
    const params = new URLSearchParams(window.location.search);
    return {
      manifest: null,
      loading: true,
      error: "",
      view: params.get("view") === "packages" ? "packages" : "setup",
      query: params.get("q") || "",
      suite: params.get("suite") || "",
      arch: params.get("arch") || "",
      component: params.get("component") || "",
      currentPage: Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1),
      setupSuite: "",
      setupArch: "",
      deb822: true,
      toast: "",
      queryTimer: null,
      toastTimer: null,
    };
  },
  computed: {
    packages() {
      return this.manifest ? this.manifest.packages : [];
    },
    suites() {
      return this.uniqueValues("suite");
    },
    arches() {
      return this.uniqueValues("arch");
    },
    components() {
      return this.uniqueValues("component");
    },
    filteredPackages() {
      const query = this.query.trim().toLocaleLowerCase();
      return this.packages.filter((item) => {
        const searchable = `${item.package}\n${item.description}\n${item.homepage || ""}`.toLocaleLowerCase();
        return (!query || searchable.includes(query))
          && (!this.suite || item.suite === this.suite)
          && (!this.arch || item.arch === this.arch)
          && (!this.component || item.component === this.component);
      });
    },
    pageCount() {
      return Math.max(1, Math.ceil(this.filteredPackages.length / PAGE_SIZE));
    },
    paginatedPackages() {
      const start = (this.currentPage - 1) * PAGE_SIZE;
      return this.filteredPackages.slice(start, start + PAGE_SIZE);
    },
    hasFilters() {
      return Boolean(this.query || this.suite || this.arch || this.component);
    },
    setupCommand() {
      if (!this.setupSuite || !this.setupArch) return "Package index is loading.";
      const baseUrl = new URL(".", window.location.href).href;
      const keyringPath = "/etc/apt/keyrings/edgeapt.gpg";
      const keyUrl = new URL("edgeapt.gpg", baseUrl).href;
      const component = this.components[0] || "main";
      const deb822Source = `Types: deb
URIs: ${baseUrl}
Suites: ${this.setupSuite}
Components: ${component}
Architectures: ${this.setupArch}
Signed-By: ${keyringPath}`;
      const legacySource = `deb [arch=${this.setupArch} signed-by=${keyringPath}] ${baseUrl} ${this.setupSuite} ${component}`;
      const sourceCommand = this.deb822
        ? `sudo tee /etc/apt/sources.list.d/edgeapt.sources >/dev/null <<'EOF'\n${deb822Source}\nEOF`
        : `echo ${this.shellQuote(legacySource)} | sudo tee /etc/apt/sources.list.d/edgeapt.list >/dev/null`;
      return `sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL ${this.shellQuote(keyUrl)} | sudo tee ${keyringPath} >/dev/null

${sourceCommand}

sudo apt update`;
    },
    exampleCommand() {
      return "sudo apt install <package-name>";
    },
  },
  mounted() {
    window.addEventListener("popstate", this.restoreUrlState);
    this.loadManifest();
  },
  beforeUnmount() {
    window.removeEventListener("popstate", this.restoreUrlState);
    window.clearTimeout(this.queryTimer);
    window.clearTimeout(this.toastTimer);
  },
  methods: {
    async loadManifest() {
      this.loading = true;
      this.error = "";
      try {
        const response = await fetch(new URL("packages.json", window.location.href));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const manifest = await response.json();
        if (manifest.schema !== "edgeapt.packages/v1" || !Array.isArray(manifest.packages)) {
          throw new Error("Unsupported package index schema");
        }
        this.manifest = manifest;
        this.suite = this.validFilter(this.suite, this.suites);
        this.arch = this.validFilter(this.arch, this.arches);
        this.component = this.validFilter(this.component, this.components);
        this.currentPage = Math.min(this.currentPage, this.pageCount);
        this.setupSuite = this.suites[0] || "";
        this.setupArch = this.arches[0] || "";
        this.syncUrl(false);
      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error);
      } finally {
        this.loading = false;
      }
    },
    uniqueValues(field) {
      return [...new Set(this.packages.map((item) => item[field]))]
        .sort((left, right) => left.localeCompare(right, undefined, { numeric: true }));
    },
    validFilter(value, values) {
      return values.includes(value) ? value : "";
    },
    packageKey(item) {
      return `${item.package}\u0000${item.version}\u0000${item.suite}\u0000${item.component}\u0000${item.arch}`;
    },
    suiteLabel(value) {
      const labels = { focal: "focal (20)", jammy: "jammy (22)", noble: "noble (24)", resolute: "resolute (26)" };
      return labels[value] || value;
    },
    formatSize(bytes) {
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
      return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
    },
    shortHash(value) {
      return `${value.slice(7, 19)}...${value.slice(-8)}`;
    },
    downloadUrl(item) {
      return new URL(item.filename, new URL(".", window.location.href)).href;
    },
    shellQuote(value) {
      return `'${value.replaceAll("'", "'\\''")}'`;
    },
    setView(view) {
      if (this.view === view) return;
      this.view = view;
      this.syncUrl(true);
    },
    scheduleQuerySync() {
      this.currentPage = 1;
      window.clearTimeout(this.queryTimer);
      this.queryTimer = window.setTimeout(() => this.syncUrl(false), 180);
    },
    commitFilterState() {
      this.currentPage = 1;
      this.syncUrl(true);
    },
    clearFilters() {
      this.query = "";
      this.suite = "";
      this.arch = "";
      this.component = "";
      this.currentPage = 1;
      this.syncUrl(true);
    },
    setPage(page) {
      this.currentPage = Math.min(Math.max(page, 1), this.pageCount);
      this.syncUrl(true);
      document.getElementById("packages-heading")?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    syncUrl(push) {
      const url = new URL(window.location.href);
      this.setParam(url, "view", this.view === "packages" ? "packages" : "");
      this.setParam(url, "q", this.query.trim());
      this.setParam(url, "suite", this.suite);
      this.setParam(url, "arch", this.arch);
      this.setParam(url, "component", this.component);
      this.setParam(
        url,
        "page",
        this.view === "packages" && this.currentPage > 1
          ? String(this.currentPage)
          : "",
      );
      window.history[push ? "pushState" : "replaceState"]({}, "", url);
    },
    setParam(url, name, value) {
      if (value) url.searchParams.set(name, value);
      else url.searchParams.delete(name);
    },
    restoreUrlState() {
      const params = new URLSearchParams(window.location.search);
      this.view = params.get("view") === "packages" ? "packages" : "setup";
      this.query = params.get("q") || "";
      this.suite = this.validFilter(params.get("suite") || "", this.suites);
      this.arch = this.validFilter(params.get("arch") || "", this.arches);
      this.component = this.validFilter(params.get("component") || "", this.components);
      const page = Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1);
      this.currentPage = Math.min(page, this.pageCount);
    },
    async copyInstall(packageName) {
      await this.copyText(`sudo apt install ${packageName}`);
    },
    async copyText(value) {
      try {
        await navigator.clipboard.writeText(value);
      } catch {
        const textarea = document.createElement("textarea");
        textarea.value = value;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }
      this.showToast("Copied to clipboard");
    },
    showToast(message) {
      this.toast = message;
      window.clearTimeout(this.toastTimer);
      this.toastTimer = window.setTimeout(() => { this.toast = ""; }, 1400);
    },
  },
}).mount("#app");

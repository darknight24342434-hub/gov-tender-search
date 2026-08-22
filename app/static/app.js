const state = {
  mode: "tenders",
  page: 1,
  scope: "all",
  grantScope: "all",
  range: "active",
  loading: false,
  requestId: 0,
};

const $ = (id) => document.getElementById(id);

function todayISO() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 10);
}

function addDaysISO(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 10);
}

function esc(value) {
  return (value == null ? "" : String(value)).replace(/[&<>"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
  }[char]));
}

function safeHref(value) {
  try {
    const url = new URL(String(value || ""), window.location.origin);
    if (["http:", "https:"].includes(url.protocol)) return url.href;
  } catch (error) {
    return "#";
  }
  return "#";
}

function tagsForScope() {
  const tags = ["每日追蹤"];
  if (state.scope === "video") tags.push("影片影音");
  if (state.scope === "publish") tags.push("出版編輯");
  if (state.scope === "aerial") tags.push("空拍");
  if (state.scope === "priority") tags.push("優先處理");
  if (state.scope === "urgent") tags.push("急件");
  return tags;
}

function applyRangeDefaults() {
  const from = $("deadline_from");
  const to = $("deadline_to");
  if (state.range === "active") {
    from.value = todayISO();
    to.value = "";
  } else if (["7", "30", "60", "365"].includes(state.range)) {
    from.value = todayISO();
    to.value = state.mode === "tenders" ? addDaysISO(Number(state.range)) : "";
  } else if (state.range === "all") {
    from.value = "";
    to.value = "";
  }
}

function buildQuery() {
  const params = new URLSearchParams();
  const q = $("q").value.trim();
  if (q) params.set("q", q);
  if (state.mode === "tenders") params.set("tags", tagsForScope().join(","));
  if (state.mode === "grants") {
    const grantScopeTags = { ai: "AI", video: "影片", culture: "文化" };
    if (grantScopeTags[state.grantScope]) params.set("tags", grantScopeTags[state.grantScope]);
  }
  if ($("deadline_from").value) params.set("deadline_from", $("deadline_from").value);
  if (state.mode === "tenders" && $("deadline_to").value) params.set("deadline_to", $("deadline_to").value);
  if (state.range !== "all") params.set("only_active", "true");
  params.set("page", String(state.page));
  return params.toString();
}

async function search() {
  const requestId = ++state.requestId;
  const requestMode = state.mode;
  state.loading = true;
  $("results").innerHTML = '<div class="empty">讀取中</div>';
  $("pager").innerHTML = "";
  try {
    const response = await fetch(`/api/search/${requestMode}?${buildQuery()}`);
    if (requestId !== state.requestId) return;
    if (response.status === 401) {
      location.href = "/login";
      return;
    }
    const data = await response.json();
    if (requestId !== state.requestId) return;
    const items = requestMode === "grants" ? filterGrantItems(data.items || []) : (data.items || []);
    renderMeta(data);
    renderResults(items);
    renderPager(data);
  } catch (error) {
    $("meta").textContent = "查詢失敗";
    $("results").innerHTML = `<div class="empty error-box">${esc(error)}</div>`;
  } finally {
    if (requestId === state.requestId) state.loading = false;
  }
}

function renderMeta(data) {
  const now = new Date().toLocaleString("zh-TW", { hour12: false });
  $("meta").textContent = `共 ${data.total || 0} 件｜第 ${data.page || 1} 頁｜畫面更新 ${now}`;
}

function formatBudget(value) {
  if (!value) return "未列預算";
  return String(value).includes("元") ? value : `${value} 元`;
}

function daysLeft(deadline) {
  if (!deadline) return null;
  const today = new Date(todayISO());
  const target = new Date(deadline);
  if (Number.isNaN(target.getTime())) return null;
  return Math.round((target - today) / 86400000);
}

function deadlineText(item) {
  if (!item.deadline) return "未列截標";
  const parts = [`${item.deadline}`];
  if (item.deadline_time) parts.push(item.deadline_time);
  const left = daysLeft(item.deadline);
  if (left != null) {
    if (left === 0) parts.push("今天");
    else if (left > 0) parts.push(`剩 ${left} 天`);
  }
  return parts.join(" · ");
}

function grantDeadlineText(item) {
  if (!item.apply_end) return "未列截止";
  const left = daysLeft(item.apply_end);
  const parts = [`${item.apply_end}`];
  if (left != null) {
    if (left === 0) parts.push("今天");
    else if (left > 0) parts.push(`剩 ${left} 天`);
    else parts.push("已截止");
  }
  return parts.join(" · ");
}

function applyPeriodText(item) {
  const start = item.apply_start || "未列起日";
  const end = item.apply_end || "未列截止";
  return `${start} ~ ${end}`;
}

function tagBadges(tags) {
  return (tags || []).map((tag) => {
    let cls = "tag";
    if (tag === "急件") cls += " danger";
    if (tag === "優先處理") cls += " important";
    if (tag === "影片影音") cls += " video";
    if (tag === "出版編輯") cls += " publish";
    if (tag === "空拍") cls += " aerial";
    return `<span class="${cls}">${esc(tag)}</span>`;
  }).join("");
}

function priorityLabel(item) {
  const tags = item.tags || [];
  if (tags.includes("急件")) return '<span class="priority danger">立即看</span>';
  if (tags.includes("優先處理")) return '<span class="priority">優先</span>';
  return '<span class="priority muted-priority">一般</span>';
}

function tenderCard(item) {
  return `<article class="tender">
    <div class="tender-main">
      <div class="title-row">
        <h2><a href="${esc(safeHref(item.url))}" target="_blank" rel="noopener">${esc(item.title || "未命名標案")}</a></h2>
        ${priorityLabel(item)}
      </div>
      <div class="facts">
        <span>${esc(item.agency || "未列機關")}</span>
        <span>案號 ${esc(item.job_number || "-")}</span>
        <span>${esc(item.type || "未列招標方式")}</span>
      </div>
      <div class="tag-row">${tagBadges(item.tags)}</div>
    </div>
    <div class="tender-side">
      <div class="deadline">${esc(deadlineText(item))}</div>
      <div class="budget">${esc(formatBudget(item.budget))}</div>
      <a class="official-link" href="${esc(safeHref(item.url))}" target="_blank" rel="noopener">官方頁</a>
    </div>
  </article>`;
}

function grantCard(item) {
  return `<article class="tender">
    <div class="tender-main">
      <div class="title-row">
        <h2><a href="${esc(safeHref(item.url))}" target="_blank" rel="noopener">${esc(item.title || "未命名補助案")}</a></h2>
        <span class="priority muted-priority">補助案</span>
      </div>
      <div class="facts">
        <span>${esc(item.agency || "未列機關")}</span>
        <span>適用對象 ${esc(item.target || "未列")}</span>
        <span>申請期間 ${esc(applyPeriodText(item))}</span>
      </div>
      <div class="tag-row">${tagBadges(item.tags)}</div>
    </div>
    <div class="tender-side">
      <div class="deadline">${esc(grantDeadlineText(item))}</div>
      <a class="official-link" href="${esc(safeHref(item.url))}" target="_blank" rel="noopener">官方頁</a>
    </div>
  </article>`;
}

function filterGrantItems(items) {
  if (state.mode !== "grants" || !["7", "30", "365"].includes(state.range)) return items;
  const maxDays = Number(state.range);
  return items.filter((item) => {
    const left = daysLeft(item.apply_end);
    return left != null && left >= 0 && left <= maxDays;
  });
}

function renderResults(items) {
  if (!items.length) {
    const label = state.mode === "grants" ? "補助案" : "標案";
    $("results").innerHTML = `<div class="empty">目前沒有符合條件的${label}</div>`;
    return;
  }
  $("results").innerHTML = items.map(state.mode === "grants" ? grantCard : tenderCard).join("");
}

function renderPager(data) {
  const page = data.page || 1;
  const totalPages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 20)));
  $("pager").innerHTML = `
    <button type="button" ${page <= 1 ? "disabled" : ""} data-page="${page - 1}">上一頁</button>
    <span>${page} / ${totalPages}</span>
    <button type="button" ${page >= totalPages ? "disabled" : ""} data-page="${page + 1}">下一頁</button>
  `;
}

function setActiveButton(selector, attr, value) {
  document.querySelectorAll(selector).forEach((button) => {
    button.classList.toggle("active", button.dataset[attr] === value);
  });
}

function rangeAppliesToMode(button) {
  const modes = button.dataset.modes;
  return !modes || modes.split(",").includes(state.mode);
}

function updateRangeChips() {
  const activeRange = document.querySelector(`.range-chip[data-range="${state.range}"]`);
  if (!activeRange || !rangeAppliesToMode(activeRange)) {
    state.range = "active";
  }
  document.querySelectorAll(".range-chip").forEach((button) => {
    button.hidden = !rangeAppliesToMode(button);
  });
  setActiveButton(".range-chip", "range", state.range);
}

function updateModeUI() {
  setActiveButton(".mode-chip", "mode", state.mode);
  $("scope-row").hidden = state.mode === "grants";
  $("grant-scope-row").hidden = state.mode !== "grants";
  $("deadline-to-field").hidden = state.mode === "grants";
  setActiveButton(".chip[data-scope]", "scope", state.scope);
  setActiveButton(".grant-chip", "grantScope", state.grantScope);
  updateRangeChips();
}

function bindEvents() {
  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.page = 1;
    search();
  });
  $("reset-btn").addEventListener("click", () => {
    $("q").value = "";
    state.scope = "all";
    state.grantScope = "all";
    state.range = "active";
    state.page = 1;
    updateModeUI();
    setActiveButton(".chip[data-scope]", "scope", state.scope);
    applyRangeDefaults();
    search();
  });
  $("reload-btn").addEventListener("click", () => search());
  document.querySelectorAll(".mode-chip").forEach((button) => {
    button.addEventListener("click", () => {
      if (state.mode === button.dataset.mode) return;
      state.mode = button.dataset.mode;
      state.scope = "all";
      state.grantScope = "all";
      state.page = 1;
      $("results").innerHTML = "";
      $("pager").innerHTML = "";
      updateModeUI();
      applyRangeDefaults();
      search();
    });
  });
  document.querySelectorAll(".chip[data-scope]").forEach((button) => {
    button.addEventListener("click", () => {
      state.scope = button.dataset.scope;
      state.page = 1;
      setActiveButton(".chip[data-scope]", "scope", state.scope);
      search();
    });
  });
  document.querySelectorAll(".grant-chip").forEach((button) => {
    button.addEventListener("click", () => {
      state.grantScope = button.dataset.grantScope;
      state.page = 1;
      setActiveButton(".grant-chip", "grantScope", state.grantScope);
      search();
    });
  });
  document.querySelectorAll(".range-chip").forEach((button) => {
    button.addEventListener("click", () => {
      state.range = button.dataset.range;
      state.page = 1;
      setActiveButton(".range-chip", "range", state.range);
      applyRangeDefaults();
      search();
    });
  });
  $("pager").addEventListener("click", (event) => {
    const page = event.target?.dataset?.page;
    if (!page) return;
    state.page = Number(page);
    search();
  });
}

applyRangeDefaults();
updateModeUI();
bindEvents();
search();

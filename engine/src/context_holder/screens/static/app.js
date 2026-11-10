// context-holder 화면 동작. 인라인 스크립트를 쓰지 않는다 (CSP: script-src 'self').
// data-copy: 지정한 <template>의 글을 클립보드에 복사한다 ("결정 기록 복사").
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const source = document.getElementById(button.dataset.copy);
  if (!source) return;
  const label = button.textContent;
  try {
    await navigator.clipboard.writeText(source.content.textContent.trim());
    button.textContent = "복사했어요";
  } catch {
    button.textContent = "복사하지 못했어요";
  }
  setTimeout(() => { button.textContent = label; }, 2400);
});

// 설정 화면의 화면 모드: 저장을 기다리지 않고 바로 바꾼다 (시스템이면 속성을 지운다).
document.addEventListener("change", (event) => {
  const choice = event.target.closest("[data-theme-choice]");
  if (!choice) return;
  if (choice.value === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = choice.value;
});

// 글 상세 모달: 누르는 즉시 열고 자리표시를 보여 준다. 내용이 오면 바꿔 끼우고, 실패하면 다시 시도를 둔다.
let opener = null;
const isModalRequest = (event) => event.detail.target && event.detail.target.id === "modal-body";
const fill = (templateId) => {
  const body = document.getElementById("modal-body");
  body.replaceChildren(document.getElementById(templateId).content.cloneNode(true));
  body.scrollTop = 0;
  return body;
};
document.addEventListener("htmx:beforeRequest", (event) => {
  if (!isModalRequest(event)) return;
  if (opener) opener.classList.remove("is-opening");
  opener = event.detail.elt;
  opener.classList.add("is-opening");
  fill("modal-loading");
  const modal = document.getElementById("article-modal");
  if (!modal.open) modal.showModal();
});
document.addEventListener("htmx:afterRequest", (event) => {
  if (!isModalRequest(event)) return;
  event.detail.elt.classList.remove("is-opening");
  if (!event.detail.successful) fill("modal-failed");
});
document.addEventListener("htmx:afterSwap", (event) => {
  if (event.target.id !== "modal-body") return;
  event.target.scrollTop = 0;
});
document.addEventListener("click", (event) => {
  const modal = document.getElementById("article-modal");
  if (!modal || !modal.open) return;
  if (event.target.closest("[data-retry]") && opener && opener.isConnected) {
    htmx.trigger(opener, "click");
    return;
  }
  if (event.target === modal || event.target.closest("[data-close]")) modal.close();
});

// 비교표: 옆으로 넘길 게 있으면 "7개 사례 중 1–2"와 앞뒤 버튼을 보이고, 오른쪽 끝을 흐리게 한다.
const columnsOf = (table) => [...table.querySelectorAll('.ch[role="columnheader"]')].slice(1);
const updateScroll = (table) => {
  const bar = table.parentElement.querySelector("[data-scroll-bar]");
  if (!bar) return;
  const columns = columnsOf(table);
  const scrollable = table.scrollWidth > table.clientWidth + 1;
  bar.hidden = !scrollable;
  table.classList.toggle("more-right", scrollable && table.scrollLeft + table.clientWidth < table.scrollWidth - 1);
  if (!scrollable) return;
  const box = table.getBoundingClientRect();
  const left = box.left + (table.querySelector(".rh")?.offsetWidth || 0);
  const shown = columns
    .map((c, i) => [c.getBoundingClientRect(), i + 1])
    .filter(([r]) => r.left >= left - 4 && r.right <= box.right + 4)
    .map(([, n]) => n);
  const range = shown.length ? `${shown[0]}–${shown[shown.length - 1]}` : "";
  bar.querySelector("[data-scroll-status]").textContent = `${columns.length}개 사례 중 ${range}`;
  bar.querySelector('[data-scroll-step="-1"]').disabled = table.scrollLeft <= 1;
  bar.querySelector('[data-scroll-step="1"]').disabled = !table.classList.contains("more-right");
};
const updateAllScrolls = () => document.querySelectorAll("[data-scroll-table]").forEach(updateScroll);
document.addEventListener("scroll", (event) => {
  if (event.target.matches?.("[data-scroll-table]")) updateScroll(event.target);
}, true);
document.addEventListener("click", (event) => {
  const step = event.target.closest("[data-scroll-step]");
  if (!step) return;
  const table = step.parentElement.parentElement.querySelector("[data-scroll-table]");
  const width = columnsOf(table)[0]?.offsetWidth || table.clientWidth / 2;
  table.scrollBy({ left: Number(step.dataset.scrollStep) * width });
});
window.addEventListener("resize", updateAllScrolls);
document.addEventListener("htmx:afterSettle", updateAllScrolls);
document.addEventListener("DOMContentLoaded", updateAllScrolls);

// 검색: 찾는 동안 결과 자리에 자리표시를 넣고, 실패하면 다시 시도를 둔다.
const searchBody = () => document.getElementById("search-body");
const isSearchRequest = (event) => event.detail.target && event.detail.target.id === "search-body";
document.addEventListener("htmx:beforeRequest", (event) => {
  if (!isSearchRequest(event)) return;
  const body = searchBody();
  const rows = document.getElementById("search-loading").content.cloneNode(true);
  const list = body.querySelector("ol.results, .results.empty");
  if (list) {  // 결과 화면: 필터는 두고 결과만 바꾼다
    const holder = document.createElement("ol");
    holder.className = "row-list results";
    holder.setAttribute("aria-busy", "true");
    holder.append(rows);
    list.replaceWith(holder);
    const meta = body.querySelector(".query-meta");
    if (meta) {
      const label = document.createElement("span");
      label.className = "searching";
      label.textContent = "찾는 중…";
      meta.replaceChildren(label);
    }
    return;
  }
  const holder = document.createElement("ol");  // 첫 화면: 통째로 바꾼다
  holder.className = "row-list results search-start";
  holder.setAttribute("aria-busy", "true");
  holder.append(rows);
  body.replaceChildren(holder);
});
document.addEventListener("htmx:afterRequest", (event) => {
  if (!isSearchRequest(event) || event.detail.successful) return;
  searchBody().replaceChildren(document.getElementById("search-failed").content.cloneNode(true));
});
document.addEventListener("click", (event) => {
  if (!event.target.closest("[data-search-retry]")) return;
  htmx.trigger(document.querySelector(".search-form"), "submit");
});

// 비교표의 사례 열 · Today 리드 글: 아무 곳이나 누르면 그 글 상세를 연다. 비교표는 같은 열 전체에 마우스 표시를 한다.
const sameColumn = (cell) => cell.closest(".matrix").querySelectorAll(`[data-article="${cell.dataset.article}"]`);
document.addEventListener("click", (event) => {
  const cell = event.target.closest(".matrix [data-article], .lead[data-article]");
  if (!cell || event.target.closest("a, button")) return;  // 링크 · 버튼은 제 일을 한다
  if (String(window.getSelection())) return;  // 글을 고르는 중이면 열지 않는다
  htmx.ajax("GET", `/article/${encodeURIComponent(cell.dataset.article)}`, { target: "#modal-body", source: cell });
});
for (const [type, on] of [["mouseover", true], ["mouseout", false]]) {
  document.addEventListener(type, (event) => {
    const cell = event.target.closest(".matrix [data-article]");
    if (!cell || (event.relatedTarget && cell.contains(event.relatedTarget))) return;
    sameColumn(cell).forEach((c) => c.classList.toggle("col-hover", on));
  });
}

// 버튼으로 시킨 일: 응답이 올 때까지 그 버튼을 끄고 돌아가는 표시를 붙인다 (두 번 누르지 않게).
const busyButtons = new Map();
const buttonOf = (event) => {
  const elt = event.detail.elt;
  if (elt.matches?.("button")) return elt;
  if (elt.matches?.("form")) return event.detail.requestConfig?.triggeringEvent?.submitter || null;
  return null;
};
document.addEventListener("htmx:beforeRequest", (event) => {
  const button = buttonOf(event);
  if (!button || !button.matches(".btn")) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  busyButtons.set(event.detail.xhr, button);
});
document.addEventListener("htmx:afterRequest", (event) => {
  const button = busyButtons.get(event.detail.xhr);
  if (!button) return;
  busyButtons.delete(event.detail.xhr);
  button.disabled = false;
  button.removeAttribute("aria-busy");
});

// Decisions: 주제를 누르면 바로 선택 표시를 옮기고, 비교표 자리에 자리표시를 넣는다.
const isDecisionsRequest = (event) => event.detail.target && event.detail.target.id === "decisions-body";
let topicOpener = null;
document.addEventListener("htmx:beforeRequest", (event) => {
  if (!isDecisionsRequest(event)) return;
  const body = document.getElementById("decisions-body");
  const topic = event.detail.elt.closest?.(".topic");
  if (!topic) {  // 주제 찾기 · 더 보기: 목록만 흐리게
    body.querySelector(".topics")?.setAttribute("aria-busy", "true");
    return;
  }
  topicOpener = topic;
  body.querySelectorAll(".topic.sel").forEach((t) => { t.classList.remove("sel"); t.removeAttribute("aria-current"); });
  topic.classList.add("sel");
  topic.setAttribute("aria-current", "true");
  body.querySelector(".main")?.replaceChildren(document.getElementById("decisions-loading").content.cloneNode(true));
});
document.addEventListener("htmx:afterRequest", (event) => {
  if (!isDecisionsRequest(event) || event.detail.successful) return;
  const body = document.getElementById("decisions-body");
  body.querySelector(".topics")?.removeAttribute("aria-busy");
  body.querySelector(".main")?.replaceChildren(document.getElementById("decisions-failed").content.cloneNode(true));
});
document.addEventListener("click", (event) => {
  if (!event.target.closest("[data-decisions-retry]") || !topicOpener?.isConnected) return;
  htmx.trigger(topicOpener, "click");
});

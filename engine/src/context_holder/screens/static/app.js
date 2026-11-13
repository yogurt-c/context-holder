// context-holder 화면 동작. 인라인 스크립트를 쓰지 않는다 (CSP: script-src 'self').
// data-copy: 지정한 <template>의 글을 클립보드에 복사한다 ("결정 기록 복사").
// 아이콘 버튼(data-copy-icon)은 글자 대신 잠깐 체크 표시와 툴팁으로 알린다.
const COPY_FEEDBACK_MS = 2400;
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const source = document.getElementById(button.dataset.copy);
  if (!source) return;
  let copied = true;
  try {
    await navigator.clipboard.writeText(source.content.textContent.trim());
  } catch {
    copied = false;
  }
  const done = copied ? "복사했어요" : "복사하지 못했어요";
  if (button.hasAttribute("data-copy-icon")) {
    const label = button.getAttribute("aria-label");
    button.classList.toggle("is-copied", copied);
    button.title = done;
    button.setAttribute("aria-label", done);
    setTimeout(() => {
      button.classList.remove("is-copied");
      button.title = label;
      button.setAttribute("aria-label", label);
    }, COPY_FEEDBACK_MS);
    return;
  }
  const label = button.textContent;
  button.textContent = done;
  setTimeout(() => { button.textContent = label; }, COPY_FEEDBACK_MS);
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

// 확인 팝업 (위키에서 제거): 상세창 위에 띄운다. 취소 · 바깥 · Esc는 팝업만 닫는다.
document.addEventListener("click", (event) => {
  const open = event.target.closest("[data-confirm-open]");
  if (open) {
    document.getElementById(open.dataset.confirmOpen)?.showModal();
    return;
  }
  const dialog = event.target.closest(".confirm-modal");
  if (dialog && (event.target === dialog || event.target.closest("[data-confirm-close]"))) {
    event.stopPropagation();
    dialog.close();
  }
}, true);

// 위키에서 제거: 지웠으면 팝업과 상세창을 닫고 보던 화면을 새로 그린다. 못 지웠으면 팝업에 이유를 보인다.
document.addEventListener("htmx:afterRequest", (event) => {
  const button = event.detail.elt;
  if (!button.matches?.("[data-remove-article]")) return;
  const dialog = button.closest(".confirm-modal");
  if (event.detail.successful) {
    dialog.close();
    document.getElementById("article-modal")?.close();
    // 글 주소에 있으면 (예전에 남은 주소) 새로 고칠 때 지운 글을 부르지 않게 첫 화면으로 간다
    if (location.pathname.startsWith("/article/")) location.assign("/");
    else location.reload();
    return;
  }
  const error = dialog.querySelector("[data-confirm-error]");
  error.textContent = event.detail.xhr?.responseText || "지우지 못했어요. 다시 시도해 주세요.";
  error.hidden = false;
});

// 북마크를 켜고 끄면 (글 상세) 열려 있는 Bookmarks 목록에서 그 줄만 숨기거나 다시 보인다.
// 다시 불러오지 않아서 펼쳐 둔 쪽과 스크롤이 그대로다. "더 보기"는 값 커서라 숨긴 줄과 상관없이 이어진다.
document.addEventListener("bookmark-changed", (event) => {
  const list = document.getElementById("bookmark-list");
  const row = list?.querySelector(`li[data-article="${CSS.escape(event.detail.id)}"]`);
  if (!row) return;
  row.hidden = !event.detail.on;
  const left = list.querySelector("li:not([hidden])") || document.getElementById("bookmark-list-more");
  document.getElementById("bookmark-results").hidden = !left;
  document.getElementById("bookmarks-empty").hidden = Boolean(left);
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
  if (event.detail.elt.closest?.("[data-topic-form]")) return;  // 주제 편집: 폼에 이유를 보인다
  body.querySelector(".main")?.replaceChildren(document.getElementById("decisions-failed").content.cloneNode(true));
});
document.addEventListener("click", (event) => {
  if (!event.target.closest("[data-decisions-retry]") || !topicOpener?.isConnected) return;
  htmx.trigger(topicOpener, "click");
});

// URL로 글 추가: 작업은 서버에서 계속하고, 모달은 상태만 조회한다.
document.addEventListener("click", (event) => {
  const modal = document.getElementById("add-modal");
  if (!modal) return;
  if (event.target.closest("[data-add-open]")) {
    document.getElementById("add-body").replaceChildren(document.getElementById("add-loading").content.cloneNode(true));
    if (!modal.open) modal.showModal();
  }
  if (event.target === modal || event.target.closest("[data-add-close]")) modal.close();
  if (event.target.closest("[data-add-another]")) {
    const state = document.getElementById("add-state");
    [...state.children].forEach((child) => { child.hidden = child.tagName !== "FORM"; });
    const input = state.querySelector("input");
    input.value = "";
    input.focus();
  }
});
// 끝난 결과는 창이나 알림을 닫으면 비운다. 진행 중이면 서버가 그대로 둔다.
function dismissAddResult() {
  htmx.ajax("POST", "/articles/add/dismiss", { target: "#add-status", swap: "innerHTML" });
}
document.getElementById("add-modal")?.addEventListener("close", dismissAddResult);
document.addEventListener("click", (event) => {
  if (event.target.closest("[data-add-dismiss]")) dismissAddResult();
});
// 추가를 시작하면 입력 창을 닫고 우측 하단 알림을 바로 띄운다.
document.addEventListener("add-started", () => {
  document.getElementById("add-modal")?.close();
  htmx.ajax("GET", "/articles/add/status", { target: "#add-status", swap: "innerHTML" });
});
// 상태가 그대로면 다시 그리지 않는다. 진행 중에 단계만 바뀌면 문구만 바꾼다:
// 다시 그리면 알림이 다시 올라오고 로딩바가 처음부터 돌아 들썩인다.
const ADD_DONE_VISIBLE_MS = 4000;
let addDoneTimer = null;
document.addEventListener("htmx:beforeSwap", (event) => {
  if (event.detail.target?.id !== "add-status" || !event.detail.xhr) return;
  const current = event.detail.target.querySelector(".add-status-note");
  const next = new DOMParser().parseFromString(event.detail.serverResponse, "text/html")
    .querySelector(".add-status-note");
  if (!current || !next || current.dataset.state !== next.dataset.state) return;
  event.detail.shouldSwap = false;
  if (current.dataset.key === next.dataset.key) return;
  current.dataset.key = next.dataset.key;
  current.querySelector(".add-status-msg").textContent =
    next.querySelector(".add-status-msg").textContent;
});
document.addEventListener("htmx:afterSwap", (event) => {
  if (event.target.id !== "add-status") return;
  clearTimeout(addDoneTimer);
  if (event.target.querySelector("[data-auto-dismiss]")) {
    addDoneTimer = setTimeout(dismissAddResult, ADD_DONE_VISIBLE_MS);
  }
});
document.addEventListener("htmx:afterSwap", (event) => {
  if (event.target.id === "add-body") {
    htmx.process(event.target);
    event.target.querySelector("form:not([hidden]) input")?.focus();
  }
});
document.addEventListener("htmx:afterRequest", (event) => {
  if (event.detail.target?.id !== "add-body" || event.detail.successful) return;
  const body = document.getElementById("add-body");
  const note = body.querySelector(".add-network-error");
  if (note) note.hidden = false;
  else body.textContent = "연결하지 못했어요. 창을 닫고 다시 열어 주세요.";
});

// 검색창 추천: 검색창과 추천 밖으로 나가면 닫는다. 추천을 누르는 동안에는 검색창 포커스를 지킨다.
const closeSuggest = (anchor) => anchor.querySelector(".suggest")?.replaceChildren();
// 불러오는 동안: 토스처럼 빛이 지나가는 자리표시. 입력하면 바로 바꾸고, 눌러서 다시 열 때는 있던 추천을 둔다
const showSuggestLoading = (box) => {
  if (!box.querySelector(".suggest-skel")) box.replaceChildren(document.getElementById("suggest-loading").content.cloneNode(true));
};
const suggestBox = (elt) => elt?.closest?.("[data-suggest]")?.querySelector(".suggest");
document.addEventListener("input", (event) => {  // 입력을 멈추기를 기다리는 동안에도 보인다
  const box = suggestBox(event.target);
  if (box) showSuggestLoading(box);
});
document.addEventListener("htmx:beforeRequest", (event) => {
  const box = event.detail.target;
  if (box?.classList.contains("suggest") && !box.children.length) showSuggestLoading(box);
});
document.addEventListener("htmx:afterRequest", (event) => {
  const box = event.detail.target;
  if (!box?.classList.contains("suggest")) return;
  if (!event.detail.successful) box.replaceChildren();  // 추천은 없어도 그만이다
});
document.addEventListener("mousedown", (event) => {
  if (event.target.closest(".suggest")) event.preventDefault();
});
document.addEventListener("focusout", (event) => {
  const anchor = event.target.closest?.("[data-suggest]");
  if (anchor && !anchor.contains(event.relatedTarget)) closeSuggest(anchor);
});
document.addEventListener("submit", (event) => {
  event.target.querySelectorAll("[data-suggest]").forEach(closeSuggest);
});

// 커서 페이징 "더 보기" (Today · Search · Decisions): 불러오는 동안 문구를 바꾸고,
// 붙은 첫 항목으로 포커스를 옮겨 이어 읽게 한다. 실패하면 다시 누를 수 있게 문구로 알린다.
let moreLoad = null;
document.addEventListener("htmx:beforeRequest", (event) => {
  const button = event.detail.elt.closest?.("[data-more]");
  if (!button) return;
  const list = document.getElementById(button.dataset.more);
  moreLoad = { list, count: list ? list.children.length : 0 };
  button.textContent = "불러오는 중…";
});
document.addEventListener("htmx:afterRequest", (event) => {
  const button = event.detail.elt.closest?.("[data-more]");
  if (!button || event.detail.successful) return;
  moreLoad = null;
  button.textContent = "불러오지 못했어요. 다시 누르기";
});
document.addEventListener("htmx:afterSettle", () => {
  if (!moreLoad) return;
  const { list, count } = moreLoad;
  moreLoad = null;
  const first = list?.children[count];
  (first?.matches("a") ? first : first?.querySelector("a"))?.focus();
});

// Decisions 머리의 이름 · 질문: 글 길이만큼 칸을 늘린다. 줄바꿈 대신 칸을 벗어나 저장한다 (폼의 change).
const autosize = (field) => {
  field.style.height = "auto";
  field.style.height = `${field.scrollHeight}px`;
};
// 분야 선택칸은 고른 분야 글 길이만큼만 넓힌다 (기본은 가장 긴 선택지 너비라 화살표가 멀어진다).
const SELECT_ARROW_PX = 30;
const measure = document.createElement("canvas").getContext("2d");
const fitSelect = (select) => {
  const style = getComputedStyle(select);
  measure.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  const text = select.options[select.selectedIndex]?.text || "";
  select.style.width = `${Math.ceil(measure.measureText(text).width) + SELECT_ARROW_PX}px`;
};
const autosizeAll = (root) => {
  root.querySelectorAll?.("[data-autosize]").forEach(autosize);
  root.querySelectorAll?.("[data-fit]").forEach(fitSelect);
};
document.addEventListener("change", (event) => {
  if (event.target.matches?.("[data-fit]")) fitSelect(event.target);
});
autosizeAll(document);
document.addEventListener("htmx:afterSettle", (event) => autosizeAll(event.target));  // 자리를 잡은 뒤에 잰다
document.addEventListener("input", (event) => {
  if (event.target.matches?.("[data-autosize]")) autosize(event.target);
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.isComposing || !event.target.matches?.("[data-autosize]")) return;
  event.preventDefault();  // 한 줄 글이다: 입력을 마치면 칸을 벗어나 저장한다
  event.target.blur();
});
window.addEventListener("resize", () => autosizeAll(document));

// 주제 편집 · 사례 옮기기에서 못 고치면 서버가 준 이유를 폼 아래에 보인다.
document.addEventListener("htmx:afterRequest", (event) => {
  const form = event.detail.elt.closest?.("[data-topic-form]");
  if (!form || event.detail.successful) return;
  const error = form.querySelector("[data-form-error]");
  const reason = event.detail.xhr?.status === 422 && event.detail.xhr.responseText;
  error.textContent = reason || "저장하지 못했어요. 앱 연결을 확인하고 다시 시도해 주세요.";
  error.hidden = false;
});

// 사례 옮기기 창: 누르는 즉시 열고, 내용이 오면 바꿔 끼운다. 옮기면 서버가 비교표를 다시 그리고 창을 닫게 한다.
const moveModal = () => document.getElementById("move-modal");
const fillMove = (templateId) => {
  document.getElementById("move-body").replaceChildren(document.getElementById(templateId).content.cloneNode(true));
};
const isMoveOpen = (event) => event.detail.target?.id === "move-body" && !event.detail.elt.closest?.("form");
document.addEventListener("htmx:beforeRequest", (event) => {
  if (!isMoveOpen(event)) return;
  fillMove("move-loading");
  if (!moveModal().open) moveModal().showModal();
});
document.addEventListener("htmx:afterRequest", (event) => {
  if (isMoveOpen(event) && !event.detail.successful) fillMove("move-failed");
});
document.addEventListener("click", (event) => {
  const modal = moveModal();
  if (!modal?.open) return;
  if (event.target === modal || event.target.closest("[data-move-close]")) modal.close();
});
document.addEventListener("topic-moved", () => moveModal()?.close());
// 새 주제 칸을 누르면 "새 주제"를, 기존 주제 목록을 누르면 "기존 주제"를 고른다.
document.addEventListener("focusin", (event) => {
  const pane = event.target.closest?.(".topic-move .move-pane");
  if (!pane) return;
  const radio = pane.closest("form").querySelector(`input[name="mode"][value="${pane.dataset.mode}"]`);
  if (radio && !radio.disabled) radio.checked = true;
});

// 사례 옮기기 · 기존 주제 찾기: 입력할 때만 아래에 맞는 주제가 뜬다 (이름 · 질문 · 분야, 띄어쓰기 · 가운뎃점 무시).
const findKey = (text) => text.toLowerCase().replace(/[\s·・]/g, "");
const showPicks = (box, open) => {
  const list = document.getElementById(box.getAttribute("aria-controls"));
  list.hidden = !open;
  box.setAttribute("aria-expanded", String(open));
};
document.addEventListener("input", (event) => {
  const box = event.target.closest?.("[data-topic-find]");
  if (!box) return;
  const key = findKey(box.value);
  const list = document.getElementById(box.getAttribute("aria-controls"));
  let shown = 0;
  list.querySelectorAll("li[data-find]").forEach((item) => {
    item.hidden = !findKey(item.dataset.find).includes(key);
    shown += item.hidden ? 0 : 1;
  });
  list.querySelector(".suggest-head").hidden = shown === 0;
  list.querySelector("[data-find-none]").hidden = shown > 0;
  showPicks(box, Boolean(key));
});
document.addEventListener("focusin", (event) => {
  const box = event.target.closest?.("[data-topic-find]");
  if (box && findKey(box.value)) showPicks(box, true);
});
const setPicked = (pane, button) => {
  pane.querySelector("[data-picked-value]").value = button ? button.dataset.pick : "";
  pane.querySelector("[data-picked]").hidden = !button;
  pane.querySelector("[data-picker]").hidden = Boolean(button);
  if (button) {
    pane.querySelector("[data-picked-name]").textContent = button.dataset.pick;
    pane.querySelector("[data-picked-meta]").textContent = button.dataset.meta;
  }
};
document.addEventListener("click", (event) => {
  document.querySelectorAll("[data-picker]").forEach((picker) => {  // 바깥을 누르면 닫는다
    if (!picker.contains(event.target)) showPicks(picker.querySelector("[data-topic-find]"), false);
  });
  const pick = event.target.closest("[data-pick]");
  const clear = event.target.closest("[data-pick-clear]");
  if (!pick && !clear) return;
  const pane = event.target.closest(".move-pane");
  pane.closest("form").querySelector('input[name="mode"][value="existing"]').checked = true;
  setPicked(pane, pick);
  if (pick) showPicks(pane.querySelector("[data-topic-find]"), false);
  else pane.querySelector("[data-topic-find]").focus();
});
// 찾는 주제가 없으면 찾던 말을 새 주제 이름으로 넣고 "새 주제"로 넘어간다.
document.addEventListener("click", (event) => {
  if (!event.target.closest("[data-new-from-find]")) return;
  const form = event.target.closest("form");
  const box = form.querySelector("[data-topic-find]");
  const name = form.querySelector("#move-name");
  form.querySelector('input[name="mode"][value="new"]').checked = true;
  name.value = box.value.trim();
  showPicks(box, false);
  name.focus();
});

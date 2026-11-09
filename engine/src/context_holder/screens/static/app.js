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

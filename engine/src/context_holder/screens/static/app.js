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

// 글 상세 모달: 내용이 들어오면 열고, 닫기 버튼이나 바깥을 누르면 닫는다.
document.addEventListener("htmx:afterSwap", (event) => {
  if (event.target.id !== "modal-body") return;
  const modal = document.getElementById("article-modal");
  if (!modal.open) modal.showModal();
  event.target.scrollTop = 0;
});
document.addEventListener("click", (event) => {
  const modal = document.getElementById("article-modal");
  if (!modal || !modal.open) return;
  if (event.target === modal || event.target.closest("[data-close]")) modal.close();
});

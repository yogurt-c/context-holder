// context-holder 화면 동작. 인라인 스크립트를 쓰지 않는다 (CSP: script-src 'self').
// data-copy: 지정한 <template>의 글을 클립보드에 복사한다 ("Claude에 문맥으로 보내기").
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const source = document.getElementById(button.dataset.copy);
  if (!source) return;
  const label = button.textContent;
  try {
    await navigator.clipboard.writeText(source.content.textContent.trim());
    button.textContent = "복사했다. Claude에 붙여 넣으세요";
  } catch {
    button.textContent = "복사하지 못했다";
  }
  setTimeout(() => { button.textContent = label; }, 2400);
});

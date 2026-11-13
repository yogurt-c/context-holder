// 선택칸: OS 기본 <select> 대신 앱 디자인의 목록을 띄운다.
// <select>는 숨겨 두고 값과 change 이벤트를 그대로 쓴다. 그래서 폼 전송과 hx-trigger="change"는 바뀌지 않는다.
// 목록 안에서는 위 · 아래 화살표로 옮기고 Enter로 고르고 Esc로 닫는다 (기본 <select>와 같은 조작).
(() => {
  const CHEVRON = '<svg class="dd-chev" aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
  const CHECK = '<svg class="dd-check" aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  const GAP_PX = 4;
  let count = 0;
  let current = null;  // 열린 선택칸 { wrap, select, button, list, active }

  const labelOf = (select) => {
    const label = select.id && document.querySelector(`label[for="${CSS.escape(select.id)}"]`);
    return select.getAttribute("aria-label") || label?.textContent.trim() || "";
  };

  const sync = (dd) => {
    const option = dd.select.options[dd.select.selectedIndex];
    dd.button.querySelector(".dd-value").textContent = option?.text || "";
    const label = labelOf(dd.select);
    dd.button.setAttribute("aria-label", label ? `${label}: ${option?.text || ""}` : option?.text || "");
    dd.list.querySelectorAll(".dd-opt").forEach((item, i) => {
      item.setAttribute("aria-selected", String(i === dd.select.selectedIndex));
    });
  };

  const setActive = (dd, index) => {
    const items = [...dd.list.querySelectorAll(".dd-opt:not([aria-disabled='true'])")];
    if (!items.length) return;
    const next = items[Math.max(0, Math.min(items.length - 1, index))];
    dd.list.querySelectorAll(".dd-opt.is-active").forEach((item) => item.classList.remove("is-active"));
    next.classList.add("is-active");
    dd.button.setAttribute("aria-activedescendant", next.id);
    next.scrollIntoView({ block: "nearest" });
    dd.active = items.indexOf(next);
  };

  // 아래 공간이 모자라면 위로 연다. 창 안의 스크롤 영역(모달 카드)에 잘리지 않게 그 경계도 본다.
  const place = (dd) => {
    dd.wrap.classList.remove("dd-up");
    const bounds = dd.wrap.closest(".modal-card")?.getBoundingClientRect();
    const bottom = Math.min(window.innerHeight, bounds?.bottom ?? Infinity);
    const top = Math.max(0, bounds?.top ?? 0);
    const rect = dd.button.getBoundingClientRect();
    const needed = dd.list.offsetHeight + GAP_PX;
    if (rect.bottom + needed > bottom && rect.top - needed >= top) dd.wrap.classList.add("dd-up");
  };

  const close = (focus = false) => {
    if (!current) return;
    const dd = current;
    current = null;
    dd.list.hidden = true;
    dd.button.setAttribute("aria-expanded", "false");
    dd.button.removeAttribute("aria-activedescendant");
    if (focus) dd.button.focus();
  };

  const open = (dd) => {
    if (current && current !== dd) close();
    current = dd;
    dd.list.hidden = false;
    dd.button.setAttribute("aria-expanded", "true");
    place(dd);
    const selected = [...dd.list.querySelectorAll(".dd-opt:not([aria-disabled='true'])")]
      .findIndex((item) => item.getAttribute("aria-selected") === "true");
    setActive(dd, Math.max(0, selected));
  };

  const choose = (dd, item) => {
    if (!item || item.getAttribute("aria-disabled") === "true") return;
    const changed = dd.select.value !== item.dataset.value;
    dd.select.value = item.dataset.value;
    sync(dd);
    close(true);
    if (changed) dd.select.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const enhance = (select) => {
    if (select.dataset.dd || select.multiple) return;
    select.dataset.dd = "on";
    count += 1;
    const id = `dd-${count}`;
    const wrap = document.createElement("span");
    wrap.className = "dd";
    const button = document.createElement("button");
    button.type = "button";
    button.id = `${id}-btn`;
    button.className = `dd-btn ${select.className}`.trim();
    button.title = select.title;
    button.disabled = select.disabled;
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", `${id}-list`);
    button.innerHTML = `<span class="dd-value"></span>${CHEVRON}`;
    const list = document.createElement("ul");
    list.id = `${id}-list`;
    list.className = "dd-list";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    [...select.options].forEach((option, i) => {
      const item = document.createElement("li");
      item.id = `${id}-opt-${i}`;
      item.className = "dd-opt";
      item.setAttribute("role", "option");
      item.dataset.value = option.value;
      if (option.disabled) item.setAttribute("aria-disabled", "true");
      item.innerHTML = CHECK;
      item.append(option.text);
      list.append(item);
    });
    select.before(wrap);
    wrap.append(select, button, list);
    select.classList.add("dd-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    const label = select.id && document.querySelector(`label[for="${CSS.escape(select.id)}"]`);
    if (label) label.htmlFor = button.id;  // 이름표를 누르면 선택칸이 잡힌다
    const dd = { wrap, select, button, list, active: 0 };
    wrap.dd = dd;
    sync(dd);
  };

  const scan = (root) => root.querySelectorAll?.("select:not([data-dd])").forEach(enhance);
  scan(document);
  document.addEventListener("htmx:load", (event) => scan(event.target));

  document.addEventListener("click", (event) => {
    const wrap = event.target.closest(".dd");
    if (!wrap) {
      close();
      return;
    }
    const dd = wrap.dd;
    if (event.target.closest(".dd-btn")) {
      if (current === dd) close();
      else open(dd);
      return;
    }
    choose(dd, event.target.closest(".dd-opt"));
  });

  document.addEventListener("mousemove", (event) => {
    const item = event.target.closest?.(".dd-opt");
    if (!item || !current || !current.list.contains(item)) return;
    const items = [...current.list.querySelectorAll(".dd-opt:not([aria-disabled='true'])")];
    if (items.indexOf(item) !== current.active) setActive(current, items.indexOf(item));
  });

  document.addEventListener("keydown", (event) => {
    const wrap = event.target.closest?.(".dd");
    if (!wrap || !event.target.matches(".dd-btn")) return;
    const dd = wrap.dd;
    const isOpen = current === dd;
    const key = event.key;
    if (!isOpen) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(key)) {
        event.preventDefault();
        open(dd);
      }
      return;
    }
    const last = dd.list.querySelectorAll(".dd-opt:not([aria-disabled='true'])").length - 1;
    const moves = { ArrowDown: dd.active + 1, ArrowUp: dd.active - 1, Home: 0, End: last };
    if (key in moves) {
      event.preventDefault();
      setActive(dd, moves[key]);
    } else if (key === "Enter" || key === " ") {
      event.preventDefault();
      choose(dd, dd.list.querySelector(".dd-opt.is-active"));
    } else if (key === "Escape") {
      event.preventDefault();  // 목록만 닫고 감싼 창(dialog)은 그대로 둔다
      event.stopPropagation();
      close(true);
    } else if (key === "Tab") {
      close();
    }
  });

  window.addEventListener("resize", () => close());
  document.addEventListener("scroll", (event) => {
    if (current && !current.list.contains(event.target)) close();
  }, true);
})();

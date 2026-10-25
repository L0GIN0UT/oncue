// Точка находки по клику на превью карты: доли изображения в % (одна цифра после запятой).
// Та же формула, что finds_form.pct_from_click в Python. Без зависимостей, без inline-кода.
(function () {
  "use strict";
  var box = document.querySelector("[data-map-pick]");
  if (!box) return;
  var active = null;

  function pct(offset, size) {
    if (size <= 0) return 0;
    var v = Math.min(Math.max((offset / size) * 100, 0), 100);
    return Math.floor(v * 10 + 0.5) / 10;
  }

  document.querySelectorAll("[data-pick-row]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      active = btn.getAttribute("data-pick-row");
      document.querySelectorAll("[data-pick-row]").forEach(function (b) {
        b.style.fontWeight = b === btn ? "bold" : "";
      });
    });
  });

  function marker(index) {
    var el = box.querySelector('[data-find-index="' + index + '"]');
    if (!el) {
      el = document.createElement("span");
      el.setAttribute("data-find-index", index);
      el.textContent = String(Number(index) + 1);
      el.style.cssText =
        "position:absolute;transform:translate(-50%,-50%);min-width:1.6em;text-align:center;" +
        "border-radius:50%;background:#2874a6;color:#fff;font-size:12px;";
      box.appendChild(el);
    }
    return el;
  }

  box.addEventListener("click", function (event) {
    if (active === null) return;
    var rect = box.getBoundingClientRect();
    var x = pct(event.clientX - rect.left, rect.width);
    var y = pct(event.clientY - rect.top, rect.height);
    var fx = document.querySelector('[data-pick-x="' + active + '"]');
    var fy = document.querySelector('[data-pick-y="' + active + '"]');
    if (fx) fx.value = String(x);
    if (fy) fy.value = String(y);
    var m = marker(active);
    m.style.left = x + "%";
    m.style.top = y + "%";
  });
})();

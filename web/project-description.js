(() => {
  "use strict";
  const buttons = [...document.querySelectorAll("[data-set-language]")];
  const sections = [...document.querySelectorAll("[data-language]")];
  const choose = (language) => {
    document.documentElement.lang = language;
    sections.forEach((section) => { section.hidden = section.dataset.language !== language; });
    buttons.forEach((button) => { button.setAttribute("aria-pressed", String(button.dataset.setLanguage === language)); });
    localStorage.setItem("oaa_description_language", language);
  };
  buttons.forEach((button) => button.addEventListener("click", () => choose(button.dataset.setLanguage)));
  const saved = localStorage.getItem("oaa_description_language");
  choose(saved === "hu" ? "hu" : "en");
})();

const form = document.querySelector("#search-form");
const searchView = document.querySelector("#search-view");
const progressView = document.querySelector("#progress-view");
const errorView = document.querySelector("#error-view");
const keywordInput = document.querySelector("#keyword");
const progressKeyword = document.querySelector("#progress-keyword");
const errorText = document.querySelector("#error-text");
const dayButtons = document.querySelectorAll("[data-days]");

let days = 14;

dayButtons.forEach((button) => {
  button.addEventListener("click", () => {
    days = Number(button.dataset.days);
    dayButtons.forEach((other) => {
      other.setAttribute("aria-pressed", other === button ? "true" : "false");
    });
  });
});

function show(view) {
  searchView.hidden = view !== "search";
  progressView.hidden = view !== "progress";
  errorView.hidden = view !== "error";
}

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const keyword = keywordInput.value.trim();
    if (!keyword) return;
    progressKeyword.textContent = keyword;
    show("progress");
    try {
      const response = await fetch("/brief", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword, days }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.url) {
        throw new Error(payload.error || "서버가 리포트를 만들지 못했습니다.");
      }
      window.location.href = payload.url;
    } catch (error) {
      errorText.textContent = error.message || "연결하지 못했습니다.";
      show("error");
    }
  });
}

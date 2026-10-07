const form = document.querySelector("#search-form");
const searchView = document.querySelector("#search-view");
const progressView = document.querySelector("#progress-view");
const errorView = document.querySelector("#error-view");
const keywordInput = document.querySelector("#keyword");
const passwordInput = document.querySelector("#password");
const progressKeyword = document.querySelector("#progress-keyword");
const errorText = document.querySelector("#error-text");
const dayButtons = document.querySelectorAll("[data-days]");

if (passwordInput) {
  passwordInput.value = localStorage.getItem("radar-password") || "";
}

let days = 14;

dayButtons.forEach((button) => {
  button.addEventListener("click", () => {
    days = Number(button.dataset.days);
    dayButtons.forEach((other) => {
      other.setAttribute("aria-pressed", other === button ? "true" : "false");
    });
  });
});

function rememberReport(payload, keyword) {
  if (!payload.html || !payload.id) return true;
  try {
    localStorage.setItem("radar-html-" + payload.id, payload.html);
    const history = JSON.parse(localStorage.getItem("radar-history") || "[]");
    history.unshift({ id: payload.id, keyword, url: payload.url });
    const seen = new Set();
    const unique = history.filter((item) => {
      if (!item || !item.id || seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
    localStorage.setItem("radar-history", JSON.stringify(unique.slice(0, 12)));
    return true;
  } catch (_error) {
    document.open();
    document.write(payload.html);
    document.close();
    return false;
  }
}

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
      const password = passwordInput ? passwordInput.value : "";
      const response = await fetch("/brief", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword, days, password }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.url) {
        throw new Error(payload.error || "서버가 리포트를 만들지 못했습니다.");
      }
      if (password) localStorage.setItem("radar-password", password);
      if (rememberReport(payload, keyword)) window.location.href = payload.url;
    } catch (error) {
      const raw = error.message || "";
      errorText.textContent = raw === "Failed to fetch"
        ? "공개 주소에 연결하지 못했습니다. 임시 배포는 한 시간이 지나면 사라집니다."
        : (raw || "연결하지 못했습니다.");
      show("error");
    }
  });
}

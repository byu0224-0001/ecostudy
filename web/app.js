const stanceButtons = document.querySelectorAll("[data-stance-filter]");
const issueButtons = document.querySelectorAll("[data-issue-filter]");
const cards = document.querySelectorAll(".video-card");
const count = document.querySelector("#video-count");
const empty = document.querySelector("#video-empty");

let stance = "all";
let issue = "all";

function matchesStance(card) {
  if (stance === "all") return true;
  if (stance === "range") {
    return card.dataset.stance === "range" || card.dataset.stance === "structural";
  }
  return card.dataset.stance === stance;
}

function matchesIssue(card) {
  if (issue === "all") return true;
  return card.dataset.issues.split(/\s+/).includes(issue);
}

function render() {
  let visible = 0;
  cards.forEach((card) => {
    const show = matchesStance(card) && matchesIssue(card);
    card.hidden = !show;
    if (show) visible += 1;
  });
  count.textContent = String(visible);
  empty.hidden = visible !== 0;
}

stanceButtons.forEach((button) => {
  button.addEventListener("click", () => {
    stance = button.dataset.stanceFilter;
    stanceButtons.forEach((other) => {
      other.setAttribute("aria-pressed", other === button ? "true" : "false");
    });
    render();
  });
});

issueButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const next = button.dataset.issueFilter;
    issue = issue === next ? "all" : next;
    issueButtons.forEach((other) => {
      const on = issue !== "all" && other === button;
      other.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const videos = document.querySelector("#videos");
    const root = document.documentElement;
    const previous = root.style.scrollBehavior;
    root.style.scrollBehavior = "auto";
    const top = videos.getBoundingClientRect().top + window.scrollY - 80;
    window.scrollTo(0, top);
    root.style.scrollBehavior = previous;
    render();
  });
});

render();

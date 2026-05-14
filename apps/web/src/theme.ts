export function isDarkMode(): boolean {
  return document.documentElement.classList.contains("dark");
}

export function toggleDarkMode(): boolean {
  const isDark = document.documentElement.classList.toggle("dark");
  localStorage.setItem("bio-harness-dark", isDark ? "1" : "0");
  return isDark;
}

export function initTheme(): void {
  const saved = localStorage.getItem("bio-harness-dark");
  if (saved === "1") {
    document.documentElement.classList.add("dark");
  }
}

import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./editor-types";
import { translate, translateDetail, type Language, type MessageKey } from "./translations";

type Theme = "light" | "dark";
export type ThemePreference = Theme | "system";
const themeKey = "nicokara.theme";
const languageKey = "nicokara.language";

function readPreference(key: string) {
  try { return localStorage.getItem(key); }
  catch { return null; }
}

function writePreference(key: string, value: string) {
  try { localStorage.setItem(key, value); }
  catch { /* The controls still work when browser storage is unavailable. */ }
}

function isThemePreference(value: unknown): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

const PreferencesContext = createContext<{
  language: Language;
  setLanguage: (language: Language) => void;
  theme: Theme;
  themePreference: ThemePreference;
  setThemePreference: (theme: ThemePreference) => void;
  t: (key: MessageKey, ...values: (string | number)[]) => string;
  detail: (text: string) => string;
} | null>(null);

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [language, updateLanguage] = useState<Language>(() => readPreference(languageKey) === "en" ? "en" : "zh-CN");
  const [selectedTheme, updateTheme] = useState<ThemePreference | null>(() => {
    const stored = readPreference(themeKey);
    return isThemePreference(stored) ? stored : null;
  });
  const [systemDark, setSystemDark] = useState(() => matchMedia("(prefers-color-scheme: dark)").matches);
  const themePreference = selectedTheme ?? "system";
  const theme = themePreference === "system" ? (systemDark ? "dark" : "light") : themePreference;

  useEffect(() => {
    if (selectedTheme !== null) return;
    let active = true;
    // Preserve the theme preference saved by an older version of the settings page.
    void api<{ theme?: unknown }>("/settings").then(({ theme }) => {
      if (active && isThemePreference(theme)) updateTheme((current) => current ?? theme);
    }).catch(() => {});
    return () => { active = false; };
  }, [selectedTheme]);

  useEffect(() => {
    const query = matchMedia("(prefers-color-scheme: dark)");
    const update = () => setSystemDark(query.matches);
    query.addEventListener("change", update);
    update();
    return () => query.removeEventListener("change", update);
  }, []);

  useLayoutEffect(() => {
    document.documentElement.lang = language;
    document.documentElement.dataset.theme = theme;
  }, [language, theme]);

  const translator = useMemo(() => ({
    t: (key: MessageKey, ...values: (string | number)[]) => translate(language, key, ...values),
    detail: (text: string) => translateDetail(language, text),
  }), [language]);

  return <PreferencesContext.Provider value={{
    language,
    setLanguage: (next) => { updateLanguage(next); writePreference(languageKey, next); },
    theme,
    themePreference,
    setThemePreference: (next) => { updateTheme(next); writePreference(themeKey, next); },
    ...translator,
  }}>{children}</PreferencesContext.Provider>;
}

export function usePreferences() {
  const preferences = useContext(PreferencesContext);
  if (!preferences) throw new Error("PreferencesProvider is missing");
  return preferences;
}

import { Languages, Moon, Sun } from "lucide-react";
import { usePreferences } from "./preferences";

export function AppHeader({ title }: { title?: string }) {
  const { language, setLanguage, theme, setThemePreference, t } = usePreferences();
  const dark = theme === "dark";
  const themeAction = t(dark ? "切换到浅色模式" : "切换到深色模式");
  return (
    <header className="topbar">
      <a href="#/projects" className="brand" aria-label="Nicokara Studio"><span className="brand-mark" aria-hidden="true">N</span><span>Nicokara Studio</span></a>
      {title && <span className="top-title" title={title}>{title}</span>}
      <div className="header-preferences">
        <button type="button" className="preference-button theme-toggle" aria-label={themeAction} title={themeAction} onClick={() => setThemePreference(dark ? "light" : "dark")}>
          {dark ? <Moon size={16} aria-hidden="true" /> : <Sun size={16} aria-hidden="true" />}
          <span>{t(dark ? "深色" : "浅色")}</span>
        </button>
        <div className="language-toggle" role="group" aria-label={t("界面语言")}>
          <Languages size={16} aria-hidden="true" />
          <button type="button" className="preference-button" lang="zh-CN" aria-pressed={language === "zh-CN"} onClick={() => setLanguage("zh-CN")}>中文</button>
          <button className="preference-button" lang="en" aria-pressed={language === "en"} onClick={() => setLanguage("en")}>English</button>
        </div>
      </div>
    </header>
  );
}

'use client';

/**
 * Theme control.
 *
 * The `<html>` class is the single source of truth (app/layout.tsx writes it
 * before first paint). This module only reflects that class into React, and
 * writes it back when the visitor switches. The choice is stored in
 * localStorage and survives reloads; with no stored choice the operating
 * system preference wins.
 */

import { useEffect, useState } from 'react';

export type Theme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'cde-theme';
const THEME_EVENT = 'cde:themechange';

function readDom(): Theme {
  if (typeof document === 'undefined') return 'dark';
  return document.documentElement.classList.contains('light') ? 'light' : 'dark';
}

function apply(theme: Theme) {
  const root = document.documentElement;
  root.classList.remove('light', 'dark');
  root.classList.add(theme);
  root.setAttribute('data-theme', theme);
  root.style.colorScheme = theme;
}

export function setTheme(theme: Theme) {
  apply(theme);
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* private mode: the theme still applies for this page view */
  }
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme }));
}

/**
 * Tracks the active theme and keeps every mounted copy in step, so two
 * toggles on one page can never disagree.
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>('dark');

  useEffect(() => {
    setThemeState(readDom());
    const onChange = (event: Event) => {
      setThemeState((event as CustomEvent<Theme>).detail ?? readDom());
    };
    window.addEventListener(THEME_EVENT, onChange);
    // The OS preference can change mid-session when nothing is stored.
    const media = window.matchMedia('(prefers-color-scheme: light)');
    const onSystem = () => {
      try {
        if (window.localStorage.getItem(THEME_STORAGE_KEY)) return;
      } catch {
        /* ignore */
      }
      const next: Theme = media.matches ? 'light' : 'dark';
      apply(next);
      setThemeState(next);
    };
    media.addEventListener('change', onSystem);
    return () => {
      window.removeEventListener(THEME_EVENT, onChange);
      media.removeEventListener('change', onSystem);
    };
  }, []);

  return { theme, setTheme };
}

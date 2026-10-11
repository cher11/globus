# Тесты

Проверяют `../index.html` в headless Chromium через Playwright.

```
cd tests
npm install            # ставит playwright; браузер: npx playwright install chromium
npm test               # правила и интерфейс (rules.test.js)
node ai-batch.js 2 6 400   # партии «компьютер против компьютера»: игроков, партий, лимит ходов
```

В игре есть отладочный доступ `window.__globus` (запуск партии, мгновенный режим без анимаций, функции движка и компьютера) — тесты работают через него.

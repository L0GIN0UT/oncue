# Макеты Guessverse v3 — спецификация для сборки бордов

Референс структуры: songless.ru (скриншоты в `C:\Users\ivanl\.claude\jobs\246d46b2\tmp\shots\`, файлы `screenshot-1793027862741-19.jpg` главная, `-22`/`-23` соло, `-25` онлайн, `-26` каталог плейлистов, `-27` карточка плейлиста (модалка), `-28` магазин, `-29` лидеры, `-30`/`-31` профиль, `-36`..`-39` игровой экран и ответ раунда, `-40` кастомные лобби, `-20` дневник события). Смотри их перед работой. Берём ИНФОРМАЦИОННУЮ АРХИТЕКТУРУ и плотность, не визуальный стиль: у нас свой стиль «Ночной эфир» (ниже).

Референс стиля: `project/Main.dc.html` (дизайн-система), `project/Game-Lifted.dc.html` (игровой экран в поднятом контрасте), `project/Event.dc.html` (интерактивный борд, пример `sc-for`/`sc-if`/state).

## Формат файла (обязательно, иначе борд не отрисуется)
Каждый борд — один файл `project/<Name>.dc.html`:
```html
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Название борда</title>
<script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
<link href="https://fonts.googleapis.com/css2?family=Onest:wght@400;500;600&family=Unbounded:wght@500;800&display=swap" rel="stylesheet">
<style>
body{margin:0;background:#1A1A1A}
/* классы страницы */
</style>
</helmet>
<div style="font-family: 'Onest', system-ui, sans-serif; color: #F2F2F2; min-height: 900px; ...">
  ...разметка...
</div>
</x-dc>
<script type="text/x-dc" data-dc-script data-props='{"$preview":{"width":1280,"height":900}}'>
class Component extends DCLogic {
  renderVals() { return {}; }
}
</script>
</body>
</html>
```
Правила: строка `<script src="./support.js"></script>` ровно такая; все элементы закрыты, атрибуты в кавычках; никаких `data:` URI, внешних картинок, `<iframe>`, emoji; иконки только inline-SVG штрихом 2 px; `{{hole}}` только как подстановка значения из `renderVals()`, без выражений; события `onClick="{{fn}}"`; списки `<sc-for list="{{items}}" as="it" hint-placeholder-count="3">`, ветки `<sc-if value="{{flag}}" hint-placeholder-val="{{true}}">`. Статические борды могут обходиться без state. Корень страницы — fluid (`max-width: 1280px; margin: 0 auto`), без фиксированной ширины в px у корня. Картинки-обложки и постеры — заглушки: `div` с CSS-градиентом и маленькой подписью «обложка» в углу, никаких реальных изображений. Текст и цифры — реалистичные, из мира проекта (треки, фильмы, ники), без lorem ipsum.

## Токены (поднятый контраст, финальные)
- фон `#1A1A1A`, поверхность `#242424`, поднятая `#2E2E2E`, линия `#474747`, линия при наведении `#6A6A6A`
- текст `#F2F2F2`, приглушённый `#ABABAB`, слабый `#6A6A6A`, второстепенный светлый `#D9D9D9`
- акцент янтарь `#F2B13B`, текст на нём `#1A1A1A`, свечение `rgba(242,177,59,.14)`; акцент один на экран (главная кнопка), остальные кнопки с границей
- исходы: точно `#27592F`/граница `#357A42`/значок `#9BE39F`; почти `#5A4516`/`#7A5F1E`/текст `#F2B13B`; мимо `#552828`/`#7A3838`/значок `#F2A0A0`; пропуск `#2E2E2E`/`#474747`/текст `#ABABAB`
- звания в лидерах: сталь `#9AA4AE`, бронза `#B5703A`, серебро `#C9CCD6`, золото `#F2B13B`, алмаз `#8FD3FF`
- шрифты: Unbounded 800 заголовки и крупные числа, Unbounded 500 подзаголовки; Onest 400/500/600 текст. Шкала 40/32/28/22/18 заголовки, 16 и 15 текст, 13 подписи, 12 служебные, 11 капители-подписи секций (`letter-spacing:.12em; text-transform:uppercase; color:#6A6A6A; font-weight:600`) — только как подпись секции карточки, как у songless «ЛЕНТА ДЕЙЛИКА»
- радиусы: карточки 20, контролы и строки 14, пилюли 999; границы 1.5 px `#474747`; теней нет, кроме свечения акцента у активного
- отступы: сетка 24 px между карточками, внутри карточки 20–24, между секциями 24

## Общая шапка сайта (копировать в каждый борд-страницу)
```html
<header style="max-width: 1280px; margin: 0 auto; padding: 14px 24px; display: flex; align-items: center; gap: 24px; box-sizing: border-box">
  <a href="Home.dc.html" style="display: flex; align-items: center; gap: 10px; flex: 1"><span style="width: 36px; height: 36px; border-radius: 10px; background: #F2B13B; display: grid; place-items: center; color: #1A1A1A"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true" style="width:20px;height:20px"><path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/></svg></span><span class="h" style="font-size: 24px; font-weight: 800; letter-spacing: -0.03em">Guessverse</span></a>
  <nav aria-label="Разделы" style="display: flex; align-items: center; gap: 8px">
    <a href="Home.dc.html" class="nav on">Главная</a><span style="color: #474747">/</span>
    <a href="Solo.dc.html" class="nav">Соло</a><span style="color: #474747">/</span>
    <a href="Online.dc.html" class="nav">Онлайн</a>
  </nav>
  <div style="flex: 1; display: flex; justify-content: flex-end; align-items: center; gap: 8px">
    <button class="ib" type="button" aria-label="Уведомления, 3 новых" style="position: relative"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 21h4"/></svg><span style="position:absolute;top:6px;right:6px;min-width:16px;height:16px;border-radius:999px;background:#F2B13B;color:#1A1A1A;font-size:10px;font-weight:700;display:grid;place-items:center">3</span></button>
    <a href="Shop.dc.html" class="pillbtn"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true" style="width:16px;height:16px;color:#F2B13B"><circle cx="12" cy="12" r="8"/><path d="M9 9.5c0-1.4 1.3-2 3-2s3 .6 3 2-1.5 1.8-3 2.3-3 1-3 2.4 1.3 2.3 3 2.3 3-.8 3-2.3"/></svg>1 250</a>
    <a href="Shop.dc.html" class="ib" aria-label="Магазин"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8h16l-1 12H5zM9 8a3 3 0 0 1 6 0"/></svg></a>
    <a href="Leaderboard.dc.html" class="ib" aria-label="Лидеры"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4h8v5a4 4 0 0 1-8 0zM6 6H4v2a3 3 0 0 0 3 3M18 6h2v2a3 3 0 0 1-3 3M12 13v4M8 21h8M9 17h6"/></svg></a>
    <a href="Profile.dc.html" class="pillbtn" style="padding-left: 4px"><span style="width: 30px; height: 30px; border-radius: 999px; border: 2px solid #F2B13B; display: grid; place-items: center; font-size: 11px; font-weight: 600">ИЛ</span>L0GIN0UT</a>
  </div>
</header>
```
и стили к ней в `<helmet><style>`:
```css
.h{font-family:"Unbounded",sans-serif}
.ico{width:22px;height:22px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:none}
.nav{font-family:"Unbounded",sans-serif;font-size:18px;font-weight:800;letter-spacing:-.02em;color:#6A6A6A;padding:8px 10px;border-radius:10px;text-decoration:none}
.nav:hover{color:#D9D9D9}
.nav.on{color:#F2B13B}
.ib{width:40px;height:40px;border-radius:999px;border:1.5px solid #474747;background:#242424;color:#ABABAB;display:grid;place-items:center;cursor:pointer;text-decoration:none}
.ib:hover{color:#F2F2F2;border-color:#6A6A6A}
.pillbtn{height:40px;padding:0 14px;border-radius:999px;border:1.5px solid #474747;background:#242424;color:#F2F2F2;display:inline-flex;align-items:center;gap:8px;font-size:14px;font-weight:600;text-decoration:none}
.card{background:#242424;border:1.5px solid #474747;border-radius:20px;padding:20px;box-sizing:border-box}
.cap{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#6A6A6A;font-weight:600}
.btn{font:inherit;font-weight:600;font-size:15px;height:44px;padding:0 18px;border-radius:14px;cursor:pointer;border:1.5px solid #474747;background:#2E2E2E;color:#F2F2F2;display:inline-flex;align-items:center;gap:8px;justify-content:center;text-decoration:none;white-space:nowrap}
.btn.go{background:#F2B13B;border-color:#F2B13B;color:#1A1A1A}
.cover{border-radius:14px;background:linear-gradient(135deg,#3A3A3A,#242424);position:relative;overflow:hidden}
.cover i{position:absolute;left:8px;bottom:6px;font-size:10px;color:#6A6A6A;font-style:normal}
a{color:#F2F2F2;text-decoration:none}
```
На странице «Соло» активен пункт «Соло», на «Онлайн» — «Онлайн», в остальных — ни один (все `#6A6A6A`), кроме главной. Подвал на страницах: одна строка 13 px `#6A6A6A`: «Поддержать проект · Telegram · Частые вопросы · О сервисе · Конфиденциальность · Соглашение · Правообладателям · 18+».

## Игровой экран: своя оболочка вместо шапки сайта
Как у songless: вверху слева кнопка «Выйти» (стрелка) и громкость, по центру знак «Guessverse» и под ним капителью название режима («Дейлик · музыка», «Случайный», «Сборник: Брит-рок 90-х»), справа счёт «0 / 30 очки». Ниже вкладки раундов 1…5 (прошедшие окрашены исходом: угадал зелёная, не угадал красная, текущая янтарная, будущие серые). Дальше шесть строк попыток (заливки исходов), лента времени с бегунком, круглая янтарная Play 88 px, поле «Название трека или исполнитель» + кнопка «Пропустить» (после шестой попытки — «Сдаться»). Колонка игры 640 px по центру. Ответ раунда — попап по макету `project/Reveal.dc.html` (обложка/постер, «Это был», название, исполнитель/режиссёр · год, две плитки попытка и очки, кнопки «В плейлист»/«В закладки» и «Продолжить»).

## Что главное в каждом борде (сценарий)
Сценарий пользователя: Главная отвечает «что сегодня» (дейлик, что сделали друзья, событие, что нового) → один клик в игру → после раунда попап с ответом → после пятого раунда итоги с наградами и предложением следующего шага (второй дейлик, дуэль, сборник) → профиль собирает прогресс. Каждая страница ведёт дальше: у пустых состояний всегда кнопка действия.

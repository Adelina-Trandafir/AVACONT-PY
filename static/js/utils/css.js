window.getClassProperty = function (className, property) {
  const temp = document.createElement('div');
  temp.className = className;
  document.body.appendChild(temp);
  const style = window.getComputedStyle(temp);
  const value = style.getPropertyValue(property);
  document.body.removeChild(temp);
  return value;
};

window.getClassNumericProperty = function (className, property) {
  const temp = document.createElement('div');
  temp.className = className;
  document.body.appendChild(temp);
  const style = window.getComputedStyle(temp);
  const value = parseFloat(style.getPropertyValue(property));
  document.body.removeChild(temp);
  return value;
};

window.getClassFontWithDefaults = function (className) {
  // 1️⃣ Creăm un element temporar
  const temp = document.createElement('div');
  temp.style.all = 'initial'; // resetează tot la valorile default CSS
  temp.className = className;
  document.body.appendChild(temp);

  // 2️⃣ Citim stilurile aplicate efectiv
  const style = window.getComputedStyle(temp);

  // 3️⃣ Compunem stringul de font
  const font = [
    style.fontStyle,
    style.fontVariant,
    style.fontWeight,
    style.fontSize,
    style.lineHeight !== 'normal' ? '/' + style.lineHeight : '',
    style.fontFamily,
  ].join(' ');

  // 4️⃣ Curățăm elementul temporar
  document.body.removeChild(temp);

  return font.trim();
};

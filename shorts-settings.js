(() => {
  const durationRange = document.getElementById('shorts-duration');
  const durationNumber = document.getElementById('shorts-duration-number');
  const watermarkRange = document.getElementById('channel-watermark-size');
  const watermarkNumber = document.getElementById('channel-watermark-size-number');
  const pair = (range, number, min, max) => {
    if (!range || !number) return;
    const sync = (source, target) => {
      const parsed = Number(source.value);
      const value = Math.max(min, Math.min(max, Number.isFinite(parsed) ? Math.round(parsed) : min));
      source.value = String(value);
      target.value = String(value);
    };
    range.addEventListener('input', () => sync(range, number));
    number.addEventListener('input', () => sync(number, range));
    number.addEventListener('change', () => sync(number, range));
    sync(number, range);
  };
  pair(durationRange, durationNumber, 10, 120);
  pair(watermarkRange, watermarkNumber, 1, 10);
})();

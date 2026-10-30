/** Country code to flag emoji (PL → 🇵🇱). */
export const flag = (country: string) =>
  country
    .toUpperCase()
    .replace(/./g, (c) => String.fromCodePoint(127397 + c.charCodeAt(0)))

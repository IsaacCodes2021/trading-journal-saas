/**
 * The limits on a trade's screenshots, as the server enforces them (`MAX_FILE_BYTES` and `MAX_PER_TRADE` in
 * `backend/tradejournal/pg/screenshots.py`). They are shown to people on the trade page and in Help; a backend test fails if
 * the server's numbers ever differ from these.
 */
export const MAX_IMAGE_MB = 10
export const MAX_IMAGES_PER_TRADE = 20

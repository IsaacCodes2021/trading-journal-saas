import './pay.css'
import { startPayment } from './pay'

const status = document.getElementById('pay-status')
startPayment(
  window,
  (text) => {
    if (status) status.textContent = text
  },
  (url) => window.location.assign(url),
)

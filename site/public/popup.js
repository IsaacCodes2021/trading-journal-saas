// The launch offer pop-up on the home page. This is the only script on the site, and it is the site's own: no cookies, no
// request to any other website.
//
// It counts what people do with the pop-up by sending the name of the event, and nothing else, to the site's own Worker
// (POST /api/e/<name>): shown, x (closed with the X), away (clicked outside it), esc (closed with the Escape key),
// copy (pressed "Copy link") and signup (pressed "Sign up now"). A visitor gets one "shown", then at most one of x, away
// or esc. That the pop-up was shown is remembered for this visit only, in session storage; it is never sent anywhere.
(function () {
  "use strict";

  var DELAY_MS = 8000;
  var SEEN = "launch-offer-seen";

  var dialog = document.getElementById("offer");
  if (!dialog || typeof dialog.showModal !== "function") return; // no <dialog>: the page is simply without the pop-up

  var signup = dialog.querySelector('[data-offer="signup"]');
  var copy = dialog.querySelector('[data-offer="copy"]');
  var close = dialog.querySelector('[data-offer="x"]');
  var status = dialog.querySelector("[data-offer-status]");
  var reason = null; // why it is being closed, set just before it is

  function count(name) {
    var url = "/api/e/" + name;
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(url)) return;
    } catch (ignored) {
      /* fall through to fetch */
    }
    try {
      fetch(url, { method: "POST", keepalive: true, credentials: "omit" });
    } catch (alsoIgnored) {
      /* counting is never worth breaking the page for */
    }
  }

  function seen() {
    try {
      return window.sessionStorage.getItem(SEEN) === "1";
    } catch (ignored) {
      return false;
    }
  }

  function markSeen() {
    try {
      window.sessionStorage.setItem(SEEN, "1");
    } catch (ignored) {
      /* private mode: it may show again on the next page load, which is acceptable */
    }
  }

  function show() {
    if (dialog.open || seen()) return;
    reason = null;
    dialog.showModal();
    markSeen();
    count("shown");
  }

  function dismiss(why) {
    if (reason !== null) return;
    reason = why;
    count(why);
  }

  if (close) {
    close.addEventListener("click", function () {
      dismiss("x");
      dialog.close();
    });
  }

  // The dialog has no padding of its own (the inner box has), so a click whose target is the dialog itself landed on the dimmed
  // area around it.
  dialog.addEventListener("click", function (event) {
    if (event.target === dialog) {
      dismiss("away");
      dialog.close();
    }
  });

  // Escape: the browser closes the dialog itself after this event.
  dialog.addEventListener("cancel", function () {
    dismiss("esc");
  });

  if (signup) {
    signup.addEventListener("click", function () {
      if (reason === null) reason = "signup";
      count("signup");
    });
  }

  if (copy) {
    copy.addEventListener("click", function () {
      count("copy");
      var link = signup ? signup.href : "";
      var done = function (words) {
        if (status) status.textContent = words;
      };
      try {
        navigator.clipboard.writeText(link).then(
          function () {
            done("Link copied. It carries the code LAUNCH10.");
          },
          function () {
            done("Could not copy. The code is LAUNCH10.");
          },
        );
      } catch (ignored) {
        done("Could not copy. The code is LAUNCH10.");
      }
    });
  }

  window.setTimeout(show, DELAY_MS);
})();

// Progressive enhancement for the waitlist form. Without this file the form still works: it posts to the same address
// and the server redirects to /thanks/. With it, the page stays put and shows the result in place. No cookies, no
// storage, and no request to any address except the form's own.
const form = document.getElementById("join");
const status = document.getElementById("join-status");

if (form && status) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    status.textContent = "Sending...";
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ email: form.elements.email.value, website: form.elements.website.value }),
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok) {
        form.reset();
        status.textContent = "Thanks. You're on the list, and you'll get one email when your invite is ready.";
        return; // the button stays disabled: one sign-up is enough
      }
      status.textContent = body.error || "Something went wrong. Please try again.";
    } catch {
      status.textContent = "Could not reach the server. Please try again.";
    }
    button.disabled = false;
  });
}

// The keyboard happy path (contracts/src/ui.md): space = play/pause, d = dispatch.
// The demo must not depend on a trackpad in front of judges, so both keys work from
// every screen: on a screen that owns the control they press it, and on a screen that
// does not they navigate to the screen that does.

const NAV_FOR = { play: "/day", dispatch: "/call" };

function inTextField(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

function activate(id) {
  const el = document.getElementById(id);
  if (el) {
    el.click();
    return;
  }
  window.location.assign(NAV_FOR[id]);
}

window.addEventListener("keydown", (ev) => {
  if (ev.metaKey || ev.ctrlKey || ev.altKey || inTextField(ev.target)) return;
  if (ev.code === "Space" || ev.key === " ") {
    ev.preventDefault();
    activate("play");
  } else if (ev.key === "d" || ev.key === "D") {
    ev.preventDefault();
    activate("dispatch");
  }
});


const pet = document.getElementById("pet");
window.pet.onState((state) => {
  pet.className = state.mode;
  if (state.mode === "alert") pet.classList.add("alert-t1");
});
pet.addEventListener("mouseenter", () => window.pet.setIgnoreMouse(false));
pet.addEventListener("mouseleave", () => window.pet.setIgnoreMouse(true));
pet.addEventListener("click", () => window.pet.openTerminal());
// Start ignoring
window.pet.setIgnoreMouse(true);

const toggleButtons = document.querySelectorAll('.toggle-btn');
const requestSections = document.querySelectorAll('.request-section');
const ACTIVE_SECTION_KEY = 'admin_active_section';

function activateSection(targetId) {
  requestSections.forEach((section) => {
    section.classList.add('hidden');
    section.classList.remove('active');
  });

  const targetSection = document.getElementById(targetId);
  if (!targetSection) {
    return false;
  }

  targetSection.classList.remove('hidden');
  targetSection.classList.add('active');
  localStorage.setItem(ACTIVE_SECTION_KEY, targetId);
  return true;
}

const currentUrl = new URL(window.location.href);
const urlSection = currentUrl.searchParams.get('section');
const storedSection = localStorage.getItem(ACTIVE_SECTION_KEY);
const defaultActiveSection = document.querySelector('.request-section.active')?.id || 'donor-section';

if (!activateSection(urlSection || storedSection || defaultActiveSection)) {
  activateSection(defaultActiveSection);
}

toggleButtons.forEach((button) => {
  button.addEventListener('click', () => {
    const targetId = button.dataset.target;
    if (!activateSection(targetId)) {
      return;
    }

    currentUrl.searchParams.set('section', targetId);
    window.history.replaceState({}, '', currentUrl);
  });
});

document.addEventListener('DOMContentLoaded', function() {
  const menuButton = document.getElementById('mobile-menu-button');
  const menu = document.getElementById('mobile-menu');
  const mainContentWrapper = document.getElementById('main-content-wrapper');

  function openMenu() {
    if (menuButton) menuButton.setAttribute('aria-expanded', 'true');
    if (menu) menu.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    if (mainContentWrapper) {
      mainContentWrapper.classList.add('blur-effect');
    }
    // Autofocus removed:
    // const focusableElements = menu.querySelectorAll('a[href]:not([disabled]), button:not([disabled]), textarea:not([disabled]), input[type="text"]:not([disabled]), input[type="radio"]:not([disabled]), input[type="checkbox"]:not([disabled]), select:not([disabled])');
    // if (focusableElements.length > 0) {
    //   focusableElements[0].focus();
    // }
  }

  function closeMenu() {
    if (menuButton) menuButton.setAttribute('aria-expanded', 'false');
    if (menu) menu.classList.add('hidden');
    document.body.style.overflow = '';
    if (mainContentWrapper) {
      mainContentWrapper.classList.remove('blur-effect');
    }
  }

  if (menuButton && menu) {
    menuButton.addEventListener('click', function() {
      const isExpanded = menuButton.getAttribute('aria-expanded') === 'true' || false;
      if (isExpanded) {
        closeMenu();
      } else {
        openMenu();
      }
    });

    // Click outside to close
    document.addEventListener('click', function(event) {
      const isMenuOpen = !menu.classList.contains('hidden');
      if (isMenuOpen && !menu.contains(event.target) && !menuButton.contains(event.target)) {
        closeMenu();
      }
    });

    // Escape key to close
    document.addEventListener('keydown', function(event) {
      if (event.key === 'Escape' && !menu.classList.contains('hidden')) {
        closeMenu();
        if (menuButton) menuButton.focus(); // Optionally refocus the menu button
      }
    });

    // Focus trapping (remains as it's good for accessibility)
    menu.addEventListener('keydown', function(event) {
      if (menu.classList.contains('hidden') || event.key !== 'Tab') return;

      const focusableElements = menu.querySelectorAll('a[href]:not([disabled]), button:not([disabled]), textarea:not([disabled]), input[type="text"]:not([disabled]), input[type="radio"]:not([disabled]), input[type="checkbox"]:not([disabled]), select:not([disabled])');
      if (focusableElements.length === 0) return;
      const firstFocusableElement = focusableElements[0];
      const lastFocusableElement = focusableElements[focusableElements.length - 1];

      if (event.shiftKey) { // Shift + Tab
        if (document.activeElement === firstFocusableElement) {
          lastFocusableElement.focus();
          event.preventDefault();
        }
      } else { // Tab
        if (document.activeElement === lastFocusableElement) {
          firstFocusableElement.focus();
          event.preventDefault();
        }
      }
    });
  }

  // Handling for the mobile services dropdown, if it exists on the page
  const mobileServicesDropdownButton = document.querySelector('[onclick="toggleMobileServicesDropdown(event)"]');
  if (mobileServicesDropdownButton) {
    // Make the function global or redefine it here if it's simple enough
    // For now, assuming toggleMobileServicesDropdown is globally available from other scripts if needed,
    // or it's specific to pages where it's defined inline.
    // If it's defined inline on each page, this script doesn't need to manage it.
    // If it needs to be centralized:
    window.toggleMobileServicesDropdown = function(event) {
        event.preventDefault();
        const dropdown = document.getElementById('mobile-services-dropdown');
        if (dropdown) {
            dropdown.classList.toggle('hidden');
            const icon = event.currentTarget.querySelector('i');
            if (icon) {
                icon.classList.toggle('fa-chevron-down');
                icon.classList.toggle('fa-chevron-up');
            }
        }
    }
  }
});

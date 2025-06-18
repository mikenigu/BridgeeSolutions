// static/js/career_form_handler.js
function handleCareerFormSubmit(formId, successContainerId, feedbackDivId) {
    const form = document.getElementById(formId);
    const successMessageContainer = document.getElementById(successContainerId);
    const feedbackDiv = document.getElementById(feedbackDivId);

    if (!form) {
        console.error(`Form handler: Form with ID '${formId}' not found.`);
        return;
    }
    const submitButton = form.querySelector('button[type="submit"]');

    if (!successMessageContainer) {
        console.error(`Form handler: Success container with ID '${successContainerId}' not found.`);
        return;
    }
    if (!feedbackDiv) {
        console.error(`Form handler: Feedback div with ID '${feedbackDivId}' not found.`);
        return;
    }
    if (!submitButton) {
        console.error(`Form handler: Submit button not found within form '${formId}'.`);
        return;
    }

    form.addEventListener('submit', function(event) {
        event.preventDefault();
        const originalButtonText = submitButton.innerHTML;
        submitButton.disabled = true;
        submitButton.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Submitting...';

        // Clear previous feedback messages
        feedbackDiv.textContent = '';
        feedbackDiv.className = 'mt-4 text-center'; // Reset feedback div classes
        successMessageContainer.classList.add('hidden'); // Ensure success message is hidden at start of new submission

        const formData = new FormData(form);

        // Use a relative URL for the fetch request
        fetch('/api/submit-application', {
            method: 'POST',
            body: formData
        })
        .then(response => {
            if (!response.ok) {
                // Try to parse error response, but fallback if it's not JSON
                return response.json().catch(() => {
                    throw new Error(`HTTP error ${response.status}`);
                }).then(errData => {
                    throw { apiError: true, data: errData, status: response.status };
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.success) {
                form.classList.add('opacity-0', 'transition-opacity', 'duration-500', 'ease-out');
                setTimeout(() => {
                    form.classList.add('hidden');
                    // No need to remove opacity classes if form is hidden, but good for cleanup if it were to be reshown without full reset
                    // form.classList.remove('opacity-0', 'transition-opacity', 'duration-500', 'ease-out');

                    successMessageContainer.classList.remove('hidden');
                    successMessageContainer.classList.add('opacity-0'); // Start transparent for fade-in
                    requestAnimationFrame(() => { // Ensure 'hidden' is removed and opacity is 0 before starting transition
                        successMessageContainer.classList.add('transition-opacity', 'duration-500', 'ease-in');
                        successMessageContainer.classList.remove('opacity-0');
                    });
                    form.reset();
                }, 500); // Duration of fade-out
                // Button remains disabled as form is hidden
            } else {
                form.classList.remove('hidden', 'opacity-0'); // Ensure form is visible for error
                successMessageContainer.classList.add('hidden');
                feedbackDiv.className = 'text-red-600 mt-4 p-3 bg-red-50 rounded-md shadow-sm';
                feedbackDiv.textContent = data.message || 'An unknown error occurred.';
                submitButton.disabled = false;
                submitButton.innerHTML = originalButtonText;
            }
        })
        .catch(error => {
            submitButton.disabled = false;
            submitButton.innerHTML = originalButtonText;
            form.classList.remove('hidden', 'opacity-0');
            successMessageContainer.classList.add('hidden');
            feedbackDiv.className = 'text-red-600 mt-4 p-3 bg-red-50 rounded-md shadow-sm';

            if (error.apiError) {
                 feedbackDiv.textContent = error.data.message || `An error occurred (Status: ${error.status}).`;
            } else {
                feedbackDiv.textContent = 'A network error occurred. Please try again.';
            }
            console.error('Error submitting form:', error);
        });
    });
}

// This function will be called from each career page individually
// e.g., in a script tag on business-analyst.html:
// document.addEventListener('DOMContentLoaded', () => {
//     if (document.getElementById('careerApplicationForm')) {
//         handleCareerFormSubmit('careerApplicationForm', 'successMessageContainerCareers', 'formFeedbackCareers');
//     }
// });

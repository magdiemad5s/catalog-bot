// static/js/api.js

const API = {
    async request(url, options = {}) {
        const defaultOptions = {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json'
            }
        };

        const finalOptions = { ...defaultOptions, ...options };
        
        // If body is an object and not FormData, stringify it
        if (finalOptions.body && typeof finalOptions.body === 'object' && !(finalOptions.body instanceof FormData)) {
            finalOptions.body = JSON.stringify(finalOptions.body);
        }

        try {
            const response = await fetch(url, finalOptions);
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || data.message || `Request failed with status ${response.status}`);
            }

            return data;
        } catch (error) {
            console.error('API Error:', error);
            throw error;
        }
    },

    async get(url) {
        return this.request(url, { method: 'GET' });
    },

    async post(url, data) {
        return this.request(url, {
            method: 'POST',
            body: data
        });
    },

    async put(url, data) {
        return this.request(url, {
            method: 'PUT',
            body: data
        });
    },

    async delete(url) {
        return this.request(url, { method: 'DELETE' });
    }
};

// Help with button loading states
const withLoading = async (btn, callback) => {
    const originalText = btn.innerHTML;
    const originalDisabled = btn.disabled;
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing...';
    
    try {
        await callback();
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = originalDisabled;
    }
};

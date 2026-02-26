import axios from 'axios';

const API_URL = 'http://127.0.0.1:8000/api/v1';

export const getBotStatus = async () => {
    try {
        const response = await axios.get(`${API_URL}/status`);
        return response.data;
    } catch (error) {
        console.error("Error fetching status:", error);
        return null;
    }
};

export const getBalance = async () => {
    try {
        const response = await axios.get(`${API_URL}/balance`);
        return response.data;
    } catch (error) {
        console.error("Error fetching balance:", error);
        return null;
    }
};

export const getTrades = async () => {
    try {
        const response = await axios.get(`${API_URL}/trades`);
        return response.data;
    } catch (error) {
        console.error("Error fetching trades:", error);
        return [];
    }
};
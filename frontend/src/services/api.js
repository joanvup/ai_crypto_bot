import axios from 'axios';

const API_URL = 'https://traderbot.colegiobilingue.edu.co/api/v1';

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

export const getTrades = async (page = 1, limit = 10, dateFilter = '') => {
    try {
        let url = `${API_URL}/trades?page=${page}&per_page=${limit}`;
        if (dateFilter) {
            url += `&filter_date=${dateFilter}`;
        }
        const response = await axios.get(url);
        return response.data; // Devuelve el objeto paginado: { data, total, page, total_pages }
    } catch (error) {
        console.error("Error fetching trades:", error);
        return { data: [], total: 0, page: 1, total_pages: 1 };
    }
};
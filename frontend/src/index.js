import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import { appLogger } from './utils/logger';

const root = ReactDOM.createRoot(document.getElementById('root'));
appLogger.info("Starting React application...");
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
appLogger.info("React application mounted");

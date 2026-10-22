import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import '@fontsource/ibm-plex-sans-condensed/500.css';
import '@fontsource/ibm-plex-sans-condensed/700.css';
import './app.css';
import { mount } from 'svelte';
import App from './App.svelte';

mount(App, { target: document.getElementById('app') });

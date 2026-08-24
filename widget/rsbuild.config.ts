import {defineConfig} from '@rsbuild/core';
import {pluginReact} from '@rsbuild/plugin-react';
import {createModuleFederationConfig, pluginModuleFederation} from '@module-federation/rsbuild-plugin';
import ConcatenatePlugin from './ConcatenatePlugin.ts';

const mfConfig = createModuleFederationConfig({
    name: 'UbiquitiNetworkWidget',
    remotes: {
        // The actual URLs are injected by the host at runtime
        // (window.dooverAdminSite_remoteUrl / window.dooverCustomerSite_remoteUrl).
        doover_admin: 'doover_admin@[window.dooverAdminSite_remoteUrl]',
        customer_site: 'customer_site@[window.dooverCustomerSite_remoteUrl]',
    },
    exposes: {
        './NetworkOverviewWidget': './src/NetworkOverviewWidget',
    },
    shared: {
        react: {singleton: true, requiredVersion: '^18.3.1', eager: true},
        'react-dom': {singleton: true, requiredVersion: '^18.3.1', eager: true},
        'react-router': {singleton: true, requiredVersion: false, eager: true},
        // doover-js is deliberately NOT shared: the widget bundles its own copy
        // so it runs against the hook versions it was written for rather than
        // whatever singleton the host happens to carry. The live client is
        // still the host's — doover-js keeps it on globalThis, so
        // peekDooverClient() returns the same instance, same socket, same auth.
        // react-query MUST stay shared, or the widget's useQueryClient() cannot
        // see the provider RemoteComponentWrapper renders.
        '@tanstack/react-query': {singleton: true, eager: true, requiredVersion: false},
    },
});

export default defineConfig({
    tools: {
        rspack: {
            plugins: [
                new ConcatenatePlugin({
                    source: './dist',
                    destination: './assets',
                    name: 'UbiquitiNetworkWidget.js',
                    ignore: ['main.js'],
                }),
            ],
        },
    },
    output: {
        injectStyles: true,
    },
    plugins: [
        pluginReact(),
        pluginModuleFederation(mfConfig),
    ],
    performance: {
        chunkSplit: {
            strategy: 'all-in-one',
        },
    },
});

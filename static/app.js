document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('config-form');
    const runBtn = document.getElementById('run-btn');
    const btnText = runBtn.querySelector('.btn-text');
    const spinner = runBtn.querySelector('.spinner');
    
    const welcomeState = document.getElementById('welcome-state');
    const resultsView = document.getElementById('results-view');
    
    const statReturn = document.getElementById('stat-return');
    const statDD = document.getElementById('stat-dd');
    const statTrades = document.getElementById('stat-trades');
    const statTime = document.getElementById('stat-time');
    
    const equityChart = document.getElementById('equity-chart');
    const summaryText = document.getElementById('summary-text');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        // UI Loading State
        runBtn.disabled = true;
        btnText.textContent = 'Running...';
        spinner.classList.remove('hidden');
        
        // Gather data
        const formData = new FormData(form);
        const data = Object.fromEntries(formData.entries());
        
        // Convert number strings to appropriate types
        data.universe_size = parseInt(data.universe_size);
        data.initial_capital = parseFloat(data.initial_capital);
        data.allocation_per_trade = parseFloat(data.allocation_per_trade);
        data.leverage = parseFloat(data.leverage);
        data.gap_target_pct = parseFloat(data.gap_target_pct);
        data.fee_pct = parseFloat(data.fee_pct);
        data.slippage_pct = parseFloat(data.slippage_pct);

        try {
            const response = await fetch('/api/run_backtest', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data),
            });

            const result = await response.json();

            if (result.status === 'success') {
                // Update Top Stats
                statReturn.textContent = `${result.return_pct}%`;
                // Color formatting based on return
                if (result.return_pct > 0) {
                    statReturn.className = 'stat-value highlight';
                } else {
                    statReturn.className = 'stat-value error';
                }

                statDD.textContent = `${result.max_drawdown}%`;
                statTrades.textContent = result.total_trades;
                statTime.textContent = `${result.execution_time_seconds}s`;

                // Update Chart and Summary
                if (result.equity_curve_b64) {
                    equityChart.src = `data:image/png;base64,${result.equity_curve_b64}`;
                }
                
                summaryText.textContent = result.summary;

                // Show Results, Hide Welcome
                welcomeState.classList.add('hidden');
                resultsView.classList.remove('hidden');
            } else {
                alert('Backtest failed: ' + result.message);
            }
            
        } catch (error) {
            console.error('Error running backtest:', error);
            alert('Failed to connect to the server. Ensure app.py is running.');
        } finally {
            // Restore UI State
            runBtn.disabled = false;
            btnText.textContent = 'Run Backtest';
            spinner.classList.add('hidden');
        }
    });
});

$(document).ready(function() {
    // --- UI Elements ---
    const form = $('#config-form');
    const runBtn = $('#run-btn');
    const btnText = runBtn.find('.btn-text');
    const spinner = runBtn.find('.spinner');
    
    const welcomeState = $('#welcome-state');
    const tabBtns = $('.tab-btn');
    const tabContents = $('.tab-content');
    
    // Stats
    const statNetPortfolio = $('#stat-net-portfolio');
    const statReturn = $('#stat-return');
    const statDD = $('#stat-dd');
    const statTargetHits = $('#stat-target-hits');
    const statSlHits = $('#stat-sl-hits');
    const statTrades = $('#stat-trades');
    const statPos = $('#stat-pos');
    const statNeg = $('#stat-neg');
    const statAvgReturn = $('#stat-avg-return');
    const statWinRate = $('#stat-win-rate');
    const statProfitFactor = $('#stat-profit-factor');
    const statTime = $('#stat-time');
    
    const equityChart = $('#equity-chart');

    // DataTables instances
    let ledgerTable = null;
    let tradesTable = null;

    // --- Tab Logic ---
    tabBtns.on('click', function() {
        tabBtns.removeClass('active');
        $(this).addClass('active');
        
        const target = $(this).data('target');
        if (!welcomeState.hasClass('hidden')) return; // Do nothing if no data yet for other tabs

        tabContents.addClass('hidden');
        $('#' + target).removeClass('hidden');
    });

    // --- Form Submission ---
    form.on('submit', async function(e) {
        e.preventDefault();
        
        // UI Loading State
        runBtn.prop('disabled', true);
        btnText.text('Running...');
        spinner.removeClass('hidden');
        
        // Gather data
        const formData = new FormData(this);
        const data = Object.fromEntries(formData.entries());
        
        // Convert number strings
        data.initial_capital = parseFloat(data.initial_capital);
        data.allocation_per_trade = parseFloat(data.allocation_per_trade);
        data.leverage = parseFloat(data.leverage);
        data.gap_target_pct = parseFloat(data.gap_target_pct);
        data.fee_pct = parseFloat(data.fee_pct);
        data.slippage_pct = parseFloat(data.slippage_pct);

        try {
            const response = await fetch('/api/run_backtest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });

            const result = await response.json();

            if (result.status === 'success') {
                updateDashboard(result);
                updateLedger(result.trades_log);
                updateTradeLog(result.trades_log);

                // Show Results, Hide Welcome
                welcomeState.addClass('hidden');
                // Trigger active tab display
                $('.tab-btn.active').click();
            } else {
                alert('Backtest failed: ' + result.message);
            }
        } catch (error) {
            console.error('Error running backtest:', error);
            alert('Failed to connect to the server. Ensure app.py is running.');
        } finally {
            runBtn.prop('disabled', false);
            btnText.text('Run Backtest');
            spinner.addClass('hidden');
        }
    });

    // --- Update Functions ---
    function updateDashboard(data) {
        const formatCurrency = (val) => '₹' + new Intl.NumberFormat('en-IN').format(val !== undefined && val !== null ? val.toFixed(2) : 0);

        $('#stat-net-portfolio').text(formatCurrency(data.ending_equity));
        $('#stat-return').text(`${data.return_pct}%`);
        $('#stat-dd').text(`${data.max_drawdown}%`);
        $('#stat-target-hits').text(data.target_hits);
        
        const slHits = data.total_trades - data.target_hits;
        $('#stat-sl-hits').text(slHits);
        
        $('#stat-trades').text(data.total_trades);
        $('#stat-pos').text(data.positive_trades);
        $('#stat-neg').text(data.negative_trades);
        
        $('#stat-avg-return').text(formatCurrency(data.avg_return_per_trade));
        $('#stat-win-rate').text(`${data.win_rate}%`);
        $('#stat-profit-factor').text(data.profit_factor);
        $('#stat-time').text(`${data.execution_time_seconds}s`);

        // New metrics
        if (data.best_gap_pct !== undefined) {
            $('#stat-best-gap').text(`${data.best_gap_pct}%`);
            $('#stat-worst-gap').text(`${data.worst_gap_pct}%`);
            $('#stat-best-day').text(formatCurrency(data.best_day_pnl));
            $('#stat-worst-day').text(formatCurrency(data.worst_day_pnl));
        }

        if (data.equity_curve_b64) {
            $('#equity-chart').attr('src', `data:image/png;base64,${data.equity_curve_b64}`);
        }
    }

    function updateLedger(trades) {
        // Group events by Date
        const eventsMap = {};
        
        trades.forEach(t => {
            // 1. BUY EVENT (on signal_date)
            const entryDate = t.signal_date;
            if (!eventsMap[entryDate]) {
                eventsMap[entryDate] = { date: entryDate, buyCount: 0, sellCount: 0, capDeployed: 0, realizedGrossP: 0, realizedGrossL: 0, realizedNetPnl: 0, capitalFreed: 0, events: [] };
            }
            eventsMap[entryDate].buyCount += 1;
            eventsMap[entryDate].capDeployed += t.allocated_capital;
            eventsMap[entryDate].events.push({
                type: 'BUY',
                symbol: t.symbol,
                time: `${entryDate} 15:30:00`,
                price: t.entry_price,
                shares: t.shares,
                value: t.allocated_capital,
                pnl: null,
                gap: null
            });

            // 2. SELL EVENT (on exit_date)
            const exitDate = t.exit_date;
            if (!eventsMap[exitDate]) {
                eventsMap[exitDate] = { date: exitDate, buyCount: 0, sellCount: 0, capDeployed: 0, realizedGrossP: 0, realizedGrossL: 0, realizedNetPnl: 0, capitalFreed: 0, events: [] };
            }
            eventsMap[exitDate].sellCount += 1;
            eventsMap[exitDate].realizedNetPnl += t.pnl;
            eventsMap[exitDate].capitalFreed += t.allocated_capital;
            if (t.pnl > 0) eventsMap[exitDate].realizedGrossP += t.pnl;
            else if (t.pnl < 0) eventsMap[exitDate].realizedGrossL += Math.abs(t.pnl);
            
            eventsMap[exitDate].events.push({
                type: 'SELL',
                symbol: t.symbol,
                time: `${exitDate} 09:15:00`,
                price: t.exit_price,
                shares: t.shares,
                value: t.allocated_capital + t.pnl,
                pnl: t.pnl,
                gap: t.gap_pct
            });
        });

        const ledgerData = Object.values(eventsMap).sort((a, b) => a.date.localeCompare(b.date));
        
        const tableData = ledgerData.map(r => {
            const netPnlPct = r.capitalFreed > 0 ? (r.realizedNetPnl / r.capitalFreed) * 100 : 0;
            return {
                "date": r.date,
                "buys": r.buyCount,
                "sells": r.sellCount,
                "cap": `₹${new Intl.NumberFormat('en-IN').format(r.capDeployed.toFixed(2))}`,
                "grossP": `<span class="highlight">₹${new Intl.NumberFormat('en-IN').format(r.realizedGrossP.toFixed(2))}</span>`,
                "grossL": `<span class="error">₹${new Intl.NumberFormat('en-IN').format(r.realizedGrossL.toFixed(2))}</span>`,
                "netPnl": `<span class="${r.realizedNetPnl > 0 ? 'highlight' : (r.realizedNetPnl < 0 ? 'error' : '')}">₹${new Intl.NumberFormat('en-IN').format(r.realizedNetPnl.toFixed(2))}</span>`,
                "netPnlPct": `<span class="${netPnlPct > 0 ? 'highlight' : (netPnlPct < 0 ? 'error' : '')}">${netPnlPct.toFixed(2)}%</span>`,
                "eventsList": r.events
            };
        });

        if (ledgerTable) {
            ledgerTable.clear().rows.add(tableData).draw();
        } else {
            ledgerTable = $('#ledger-table').DataTable({
                data: tableData,
                columns: [
                    {
                        "className": 'details-control',
                        "orderable": false,
                        "data": null,
                        "defaultContent": ''
                    },
                    { "data": "date" },
                    { "data": "buys" },
                    { "data": "sells" },
                    { "data": "cap" },
                    { "data": "grossP" },
                    { "data": "grossL" },
                    { "data": "netPnl" },
                    { "data": "netPnlPct" }
                ],
                pageLength: 20,
                order: [[1, 'asc']],
            });

            // Add event listener for opening and closing details
            $('#ledger-table tbody').on('click', 'td.details-control', function () {
                var tr = $(this).closest('tr');
                var row = ledgerTable.row(tr);
        
                if (row.child.isShown()) {
                    // This row is already open - close it
                    row.child.hide();
                    tr.removeClass('shown');
                } else {
                    // Open this row
                    row.child(formatChild(row.data())).show();
                    tr.addClass('shown');
                }
            });
        }
    }

    function formatChild(d) {
        const formatMoney = (val) => new Intl.NumberFormat('en-IN').format(val);
        let html = '<table class="child-table" cellpadding="5" cellspacing="0" border="0">';
        html += '<thead><tr><th>Symbol</th><th>Action</th><th>Time</th><th>Price</th><th>Qty</th><th>Value (₹)</th><th>Gap %</th><th>Realized P&L (₹)</th></tr></thead><tbody>';
        
        // Sort events by time ascending
        d.eventsList.sort((a, b) => a.time.localeCompare(b.time));

        d.eventsList.forEach(e => {
            const actionHtml = e.type === 'BUY' ? `<span class="highlight">BUY</span>` : `<span class="error">SELL</span>`;
            const gapHtml = e.gap !== null ? `<span class="${e.gap > 0 ? 'highlight' : (e.gap < 0 ? 'error' : '')}">${e.gap.toFixed(2)}%</span>` : '-';
            const pnlHtml = e.pnl !== null ? `<span class="${e.pnl > 0 ? 'highlight' : (e.pnl < 0 ? 'error' : '')}">₹${formatMoney(e.pnl.toFixed(2))}</span>` : '-';

            html += `<tr>
                <td>${e.symbol}</td>
                <td>${actionHtml}</td>
                <td>${e.time}</td>
                <td>₹${e.price.toFixed(2)}</td>
                <td>${e.shares}</td>
                <td>₹${formatMoney(e.value.toFixed(2))}</td>
                <td>${gapHtml}</td>
                <td>${pnlHtml}</td>
            </tr>`;
        });
        
        html += '</tbody></table>';
        return html;
    }

    function updateTradeLog(trades) {
        const formatMoney = (val) => new Intl.NumberFormat('en-IN').format(val);
        const tableData = trades.map(t => [
            t.symbol,
            `<span class="highlight">BUY</span>`,
            `${t.signal_date} 15:30:00`,
            `₹${t.entry_price.toFixed(2)}`,
            `${t.exit_date} 09:15:00`,
            `₹${t.exit_price.toFixed(2)}`,
            `<span class="${t.gap_pct > 0 ? 'highlight' : (t.gap_pct < 0 ? 'error' : '')}">${t.gap_pct.toFixed(2)}%</span>`,
            t.shares,
            `₹${formatMoney(t.allocated_capital.toFixed(2))}`,
            `<span class="${t.pnl > 0 ? 'highlight' : (t.pnl < 0 ? 'error' : '')}">₹${formatMoney(t.pnl.toFixed(2))}</span>`
        ]);

        if (tradesTable) {
            tradesTable.clear().rows.add(tableData).draw();
        } else {
            tradesTable = $('#trades-table').DataTable({
                data: tableData,
                pageLength: 20,
                order: [[2, 'asc']], // Sort by entry time
            });
        }
    }
});

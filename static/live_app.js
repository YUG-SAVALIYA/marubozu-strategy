$(document).ready(function() {
    let openTable = null;
    let historyTable = null;
    let signalsTable = null;

    const formatMoney = (val) => new Intl.NumberFormat('en-IN').format(val !== undefined && val !== null ? val.toFixed(2) : 0);

    // Tab Logic
    $('.tab-btn').on('click', function() {
        $('.tab-btn').removeClass('active');
        $(this).addClass('active');
        
        const target = $(this).data('target');
        $('.tab-content').addClass('hidden');
        $('#' + target).removeClass('hidden');
    });

    // Fetch Initial Data
    fetchPortfolio();
    fetchPaperTrades();
    fetchLiveSignals();

    // Polling every 30 seconds
    setInterval(() => {
        fetchPortfolio();
        fetchPaperTrades();
        fetchLiveSignals();
    }, 30000);

    // API Calls
    async function fetchPortfolio() {
        try {
            const res = await fetch('/api/portfolio');
            const data = await res.json();
            if (data.status === 'success') {
                const p = data.portfolio;
                $('#stat-equity').text(`₹${formatMoney(p.total_equity)}`);
                $('#stat-capital').text(`₹${formatMoney(p.available_capital)}`);
                
                const pnl = p.total_equity - 100000;
                const pnlElem = $('#stat-pnl');
                pnlElem.text(`${pnl >= 0 ? '+' : ''}₹${formatMoney(pnl)}`);
                pnlElem.removeClass('highlight error');
                if (pnl > 0) pnlElem.addClass('highlight');
                else if (pnl < 0) pnlElem.addClass('error');
            }
        } catch (e) {
            console.error(e);
        }
    }

    async function fetchPaperTrades() {
        try {
            const res = await fetch('/api/paper_trades');
            const data = await res.json();
            if (data.status === 'success') {
                const trades = data.trades;
                const openTrades = trades.filter(t => t.status === 'OPEN');
                const closedTrades = trades.filter(t => t.status === 'CLOSED');
                
                updateOpenTable(openTrades);
                updateHistoryTable(closedTrades);
            }
        } catch (e) {
            console.error(e);
        }
    }

    async function fetchLiveSignals() {
        try {
            const res = await fetch('/api/live_signals');
            const data = await res.json();
            if (data.status === 'success') {
                updateSignalsTable(data.signals);
            }
        } catch (e) {
            console.error(e);
        }
    }

    // Trigger Buttons
    $('#btn-trigger-buy').on('click', async function() {
        const btn = $(this);
        btn.prop('disabled', true).text('Scanning...');
        try {
            const res = await fetch('/api/trigger_buy', { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                alert(`Scan complete. Found ${data.signals_generated} signals.`);
                fetchPortfolio();
                fetchPaperTrades();
                fetchLiveSignals();
            }
        } catch (e) {
            alert('Failed to trigger buy scan.');
        } finally {
            btn.prop('disabled', false).text('Trigger 3:20 PM Scan (BUY)');
        }
    });

    $('#btn-trigger-sell').on('click', async function() {
        const btn = $(this);
        btn.prop('disabled', true).text('Selling...');
        try {
            const res = await fetch('/api/trigger_sell', { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                alert(`Sell complete. Closed ${data.closed_trades} trades.`);
                fetchPortfolio();
                fetchPaperTrades();
            }
        } catch (e) {
            alert('Failed to trigger sell scan.');
        } finally {
            btn.prop('disabled', false).text('Trigger 9:15 AM Scan (SELL)');
        }
    });

    // DataTables rendering
    function updateOpenTable(trades) {
        const tableData = trades.map(t => [
            t.symbol,
            t.entry_date,
            `₹${t.entry_price.toFixed(2)}`,
            t.quantity,
            `₹${formatMoney(t.invested_amount)}`,
            `<span class="highlight">OPEN</span>`
        ]);

        if (openTable) {
            openTable.clear().rows.add(tableData).draw();
        } else {
            openTable = $('#open-table').DataTable({ data: tableData, order: [[1, 'desc']] });
        }
    }

    function updateHistoryTable(trades) {
        const tableData = trades.map(t => [
            t.symbol,
            t.entry_date,
            t.exit_date,
            `₹${t.entry_price.toFixed(2)}`,
            `₹${t.exit_price.toFixed(2)}`,
            t.quantity,
            `₹${formatMoney(t.invested_amount)}`,
            `<span class="${t.pnl > 0 ? 'highlight' : (t.pnl < 0 ? 'error' : '')}">₹${formatMoney(t.pnl)}</span>`
        ]);

        if (historyTable) {
            historyTable.clear().rows.add(tableData).draw();
        } else {
            historyTable = $('#history-table').DataTable({ data: tableData, order: [[2, 'desc']] });
        }
    }

    function updateSignalsTable(signals) {
        const tableData = signals.map(s => [
            s.symbol,
            s.signal_date,
            `₹${s.entry_price.toFixed(2)}`,
            `<span class="badge" style="background: rgba(0, 242, 254, 0.1); color: #00f2fe; padding: 4px 8px; border-radius: 4px;">${s.filter_group}</span>`,
            (s.st_triggered || []).join(', '),
            s.atr_pct ? s.atr_pct.toFixed(2) + '%' : '-',
            s.avg20_turnover_cr ? s.avg20_turnover_cr.toFixed(2) : '-'
        ]);

        if (signalsTable) {
            signalsTable.clear().rows.add(tableData).draw();
        } else {
            signalsTable = $('#signals-table').DataTable({ data: tableData, order: [[0, 'asc']] });
        }
    }
});

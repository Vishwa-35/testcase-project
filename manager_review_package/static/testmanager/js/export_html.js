/* =========================================================
   EXPORT PAGE – SHARE, FILTERS, CHARTS & KPI LOGIC
   ========================================================= */

/* -------------------------------
   SHARE FUNCTIONALITY
-------------------------------- */

function copyLink() {
    copyLinkFromInput();
}

function copyLinkFromInput() {
    const shareLinkInput = document.getElementById('shareLinkInput');
    const url = shareLinkInput ? shareLinkInput.value : window.location.href;
    const modalEl = document.getElementById('shareModal');
    const modal = modalEl ? bootstrap.Modal.getInstance(modalEl) : null;

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url)
            .then(() => {
                showCopyFeedback('Link copied to clipboard!');
                updateCopyButtonIcon(true);
            })
            .catch(() => {
                fallbackCopy(url, modal);
            });
    } else {
        fallbackCopy(url, modal);
    }
}

function updateCopyButtonIcon(success) {
    const buttons = document.querySelectorAll('.share-link-copy-btn, .copy-icon');
    buttons.forEach(btn => {
        const icon = btn.querySelector('i');
        if (!icon) return;

        if (success) {
            icon.className = 'bi bi-check';
            setTimeout(() => {
                icon.className = 'bi bi-clipboard';
            }, 2000);
        }
    });
}

function fallbackCopy(url, modal) {
    try {
        const textarea = document.createElement('textarea');
        textarea.value = url;
        textarea.style.position = 'fixed';
        textarea.style.left = '-9999px';
        textarea.style.opacity = '0';

        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();

        const success = document.execCommand('copy');
        document.body.removeChild(textarea);

        if (success) {
            showCopyFeedback('Link copied to clipboard!');
        } else {
            showCopyFeedback('Copy failed. Please copy manually.', 'error');
        }

        if (modal) modal.hide();
    } catch (err) {
        showCopyFeedback('Copy failed. Please copy manually.', 'error');
        if (modal) modal.hide();
    }
}

function showCopyFeedback(message, type = 'success') {
    let el = document.getElementById('copyFeedback');

    if (!el) {
        el = document.createElement('div');
        el.id = 'copyFeedback';
        el.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 14px 20px;
            border-radius: 8px;
            z-index: 10000;
            font-weight: 500;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            animation: slideIn 0.3s ease;
        `;
        document.body.appendChild(el);
    }

    el.textContent = message;
    el.style.backgroundColor = type === 'error' ? '#f8d7da' : '#d4edda';
    el.style.color = type === 'error' ? '#721c24' : '#155724';
    el.style.border = `1px solid ${type === 'error' ? '#f5c6cb' : '#c3e6cb'}`;

    setTimeout(() => {
        el.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => el.remove(), 300);
    }, 3000);
}

/* Inject animation styles once */
if (!document.getElementById('copyFeedbackStyles')) {
    const style = document.createElement('style');
    style.id = 'copyFeedbackStyles';
    style.textContent = `
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to   { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideOut {
            from { transform: translateX(0); opacity: 1; }
            to   { transform: translateX(100%); opacity: 0; }
        }
    `;
    document.head.appendChild(style);
}

function shareToTeams() {
    const url = encodeURIComponent(window.location.href);
    bootstrap.Modal.getInstance(document.getElementById('shareModal'))?.hide();
    window.open(`https://teams.microsoft.com/share?href=${url}`, '_blank');
}

function shareToOutlook() {
    const subject = encodeURIComponent('Test Cases Export');
    const body = encodeURIComponent(`Check out this test cases export:\n${window.location.href}`);
    bootstrap.Modal.getInstance(document.getElementById('shareModal'))?.hide();
    window.location.href = `mailto:?subject=${subject}&body=${body}`;
}

async function shareNearby() {
    const modal = bootstrap.Modal.getInstance(document.getElementById('shareModal'));

    if (navigator.share) {
        try {
            await navigator.share({
                title: 'Test Cases Export',
                text: 'Check out this test cases export',
                url: window.location.href
            });
            modal?.hide();
        } catch {
            copyLink();
            modal?.hide();
        }
    } else {
        copyLink();
        modal?.hide();
    }
}

/* -------------------------------
   CHART INITIALIZATION
-------------------------------- */

document.addEventListener('DOMContentLoaded', () => {
    if (!window.Chart) return;

    const safeParse = id => {
        const el = document.getElementById(id);
        if (!el) return null;
        try { return JSON.parse(el.textContent); } catch { return null; }
    };

    const statusLabels = safeParse("statusLabels");
    const statusValues = safeParse("statusValues");
    const sheetLabels = safeParse("sheetLabels");
    const sheetValues = safeParse("sheetValues");
    const barMode = safeParse("barMode") || "total_per_sheet";

    /* PIE CHART */
    const pieEl = document.getElementById("statusPie");
    if (pieEl && statusLabels?.length) {
        new Chart(pieEl, {
            type: "pie",
            data: {
                labels: statusLabels,
                datasets: [{
                    data: statusValues,
                    backgroundColor: ["#28b70b", "#ff0202", "#fdf90e", "#9ca3af"]
                }]
            },
            options: { responsive: true, plugins: { legend: { position: "bottom" } } }
        });
    }

    /* BAR CHART - Dynamic based on filter state */
    const barEl = document.getElementById("swBarChart");
    const barChartTitleEl = document.getElementById("barChartTitleText");
    
    if (barEl && sheetLabels?.length) {
        let chartConfig;
        let chartTitle;

        if (barMode === "status_overview") {
            // Sheet + SW selected: Show grouped bars (PASS/FAIL/NOT RELEVANT)
            chartTitle = "Status Overview";
            
            // sheetValues contains: [total, pass, fail, not_relevant]
            const totalValue = sheetValues[0] || 0;
            const passValue = sheetValues[1] || 0;
            const failValue = sheetValues[2] || 0;
            const notRelevantValue = sheetValues[3] || 0;

            chartConfig = {
                type: "bar",
                data: {
                    labels: ["Status Breakdown"],
                    datasets: [
                        { 
                            label: "Pass", 
                            data: [passValue], 
                            backgroundColor: "#28a745",
                            borderColor: "#1e7e34",
                            borderWidth: 1
                        },
                        { 
                            label: "Fail", 
                            data: [failValue], 
                            backgroundColor: "#dc3545",
                            borderColor: "#bd2130",
                            borderWidth: 1
                        },
                        { 
                            label: "Not Relevant", 
                            data: [notRelevantValue], 
                            backgroundColor: "#17a2b8",
                            borderColor: "#138496",
                            borderWidth: 1
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { 
                        legend: { 
                            position: "top",
                            display: true
                        },
                        tooltip: {
                            callbacks: {
                                afterLabel: function(context) {
                                    return `Total: ${totalValue}`;
                                }
                            }
                        }
                    },
                    scales: { 
                        y: { 
                            beginAtZero: true, 
                            ticks: { 
                                precision: 0,
                                stepSize: 1
                            },
                            title: {
                                display: true,
                                text: 'Number of Test Cases'
                            }
                        },
                        x: { 
                            ticks: { 
                                maxRotation: 0, 
                                minRotation: 0 
                            }
                        }
                    },
                    interaction: {
                        mode: 'index',
                        intersect: false
                    }
                }
            };
        } else if (barMode === "total_per_sw") {
            // Sheet selected: Show SW Part Number-wise totals
            chartTitle = "SW Part Number Distribution";
            
            chartConfig = {
                type: "bar",
                data: {
                    labels: sheetLabels,
                    datasets: [{
                        label: "Test Cases",
                        data: sheetValues,
                        backgroundColor: "#0d6efd",
                        borderColor: "#0a58ca",
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: { 
                        y: { 
                            beginAtZero: true, 
                            ticks: { 
                                precision: 0,
                                stepSize: 1
                            },
                            title: {
                                display: true,
                                text: 'Number of Test Cases'
                            }
                        },
                        x: { 
                            ticks: { 
                                maxRotation: 45, 
                                minRotation: 45 
                            },
                            title: {
                                display: true,
                                text: 'SW Part Number'
                            }
                        }
                    },
                    plugins: { 
                        legend: { 
                            display: false 
                        }
                    }
                }
            };
        } else {
            // No filter: Show Sheet-wise totals (default)
            chartTitle = "Sheet Distribution";
            
            chartConfig = {
                type: "bar",
                data: {
                    labels: sheetLabels,
                    datasets: [{
                        label: "Test Cases",
                        data: sheetValues,
                        backgroundColor: "#0d6efd",
                        borderColor: "#0a58ca",
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: { 
                        y: { 
                            beginAtZero: true, 
                            ticks: { 
                                precision: 0,
                                stepSize: 1
                            },
                            title: {
                                display: true,
                                text: 'Number of Test Cases'
                            }
                        },
                        x: { 
                            ticks: { 
                                maxRotation: 45, 
                                minRotation: 45 
                            },
                            title: {
                                display: true,
                                text: 'Sheet Name'
                            }
                        }
                    },
                    plugins: { 
                        legend: { 
                            display: false 
                        }
                    }
                }
            };
        }

        // Update chart title
        if (barChartTitleEl) {
            barChartTitleEl.textContent = chartTitle;
        }

        // Create chart
        new Chart(barEl, chartConfig);
    }

    /* KPI CIRCULAR PROGRESS */
    function drawCircularProgress(id, percentage, color) {
        const canvas = document.getElementById(id);
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const r = 40, lw = 8;
        const cx = canvas.width / 2;
        const cy = canvas.height / 2;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.strokeStyle = '#e0e0e0';
        ctx.lineWidth = lw;
        ctx.stroke();

        const angle = (percentage / 100) * Math.PI * 2 - Math.PI / 2;
        ctx.beginPath();
        ctx.arc(cx, cy, r, -Math.PI / 2, angle);
        ctx.strokeStyle = color;
        ctx.lineWidth = lw;
        ctx.lineCap = 'round';
        ctx.stroke();
    }

    drawCircularProgress('passProgress', window.PASS_PERCENTAGE || 0, '#28a745');
    drawCircularProgress('failProgress', window.FAIL_PERCENTAGE || 0, '#dc3545');
    drawCircularProgress('notExecProgress', window.NOT_EXEC_PERCENTAGE || 0, '#ffc107');
    drawCircularProgress('totalProgress', window.EXECUTED_PERCENTAGE || 0, '#0052A5');
});

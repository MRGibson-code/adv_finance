import numpy as np
import pandas as pd

from adv_finance.multiprocess.multiprocess import mp_pandas_obj


# Snippet 3.2, page 45, Triple Barrier Labeling Method
def apply_pt_sl_on_t1(close, events, pt_sl, molecule):
    """
    Snippet 3.2, page 45, Triple Barrier Labeling Method

    This function applies the triple-barrier labeling method. It works on a set of
    datetime index values (molecule). This allows the program to parallelize the processing.

    Mainly it returns a DataFrame of timestamps regarding the time when the first barriers were reached.

    :param close: (series) close prices
    :param events: (series) of indices that signify "events" (see cusum_filter function for more details)
    :param pt_sl: (array) element 0, indicates the profit taking level; element 1 is stop loss level
    :param molecule: (an array) a set of datetime indes values for processing
    :return: DataFrame of timestamps of when first barrier was touched
    """

    # Apply stop loss/profit taking, if it takes place before t1 (end of event)
    events_ = events.loc[molecule]
    out = events_[['t1']].copy(deep=True)

    profit_taking_multiple = pt_sl[0]
    stop_loss_multiple = pt_sl[1]

    # Profit taking active
    if profit_taking_multiple > 0:
        profit_taking = profit_taking_multiple * events_['trgt']
    else:
        profit_taking = pd.Series(index=events.index) # NaNs

    # Stop loss active
    if stop_loss_multiple > 0:
        stop_loss = -stop_loss_multiple * events_['trgt']
    else:
        stop_loss = pd.Series(index=events.index) # NaNs

    # Get events
    for loc, vertical_barrier in events_['t1'].fillna(close.index[-1]).iteritems():
        df0 = close[loc:vertical_barrier]   # path prices
        df0 = (df0 / close[loc] - 1) * events_.at[loc, 'side']  # path returns
        out.loc[loc, 'sl'] = df0[df0 < stop_loss[loc]].index.min()  # earliest stop loss
        out.loc[loc, 'pt'] = df0[df0 > profit_taking[loc]].index.min()  # earliest profit taking

    return out


# Snippet 3.4 page 49, Adding a Vertical Barrier
def add_vertical_barrier(t_events, close, num_days=1):
    """
    Snippet 3.4 page 49, Adding a Vertical Barrier

    For each index in t_events, it finds the timestamp of the next price bar at or immediately after
    a number of days num_days. This vertical barrier can be passed as an optional argument t1 in get_events.

    This function creates a series that has all the timestamps of when the vertical barrier would be reached.

    : param t_events: (series) series of events(symmetric CUSUM filter)
    : param close: (series) close prices
    : param num_days: (int) maximum number of days a trade can be active
    : return: (series) timestamps of vertical barriers
    """
    # Find index to closest to vertical barrier
    nearest_index = close.index.searchsorted(t_events + pd.Timedelta(days=num_days))

    # Exclude indexes which are outside the range of close price index
    nearest_index = nearest_index[nearest_index < close.shape[0]]

    # Find price index closest to vertical barrier time stamp
    nearest_timestamp = close.index[nearest_index]
    filtered_events = t_events[:nearest_index.shape[0]]

    vertical_barriers = pd.Series(data=nearest_timestamp, index=filtered_events)
    return vertical_barriers


# Snippet 3.3 -> 3.6 page 50, Getting the Time of the First Touch, with Meta Labels
def get_events(close, t_events, pt_sl, target, min_ret, num_threads, vertical_barrier_times=False, side_prediction=None):
    """
    Snippet 3.6 page 50, Getting the Time of the First Touch, with Meta Labels

    This function is orchestrator to meta-label the data, in conjunction with the Triple Barrier Method.

    :param close: (series) Close prices
    :param t_events: (series) of t_events, These are timestamps that will seed every triple barrier.
        These are the timestamps selected by the sampling procedures discussed in Chapter 2, Section 2.5.
        Eg: CUSUM Filter
    :param pt_sl: (2 element array) element 0, indicates the profit taking level; element 1 is stop loss level.
        A non-negative float that sets the width of the two barriers. A 0 value means that the respective
        horizontal barrier (profit taking and/or stop loss) will be disabled.
    :param target: (series) of values that are used (in conjunction with pt_sl) to determine the width
        of the barrier. In this program this is daily volatility series.
    :param min_ret: (float) The minimum target return required for running a triple barrier search.
    :param num_threads: (int) The number of threads concurrently used by the function.
    :param vertical_barrier_times: (series) A pandas series with the timestamps of the vertical barriers.
        We pass a False when we want to disable vertical barriers.
    :param side_prediction: (series) Side of the bet(long/short) as decided by the primary model
    :return: (data frame) of events
        - events.index is event's starttime
        - events['t1'] is event's endtime
        - events['trgt'] is event's target
        - events['side'] (optional) implies the algo's position side
    """

    # 1) Get target
    target = target.loc[t_events]
    target = target[target > min_ret]   # min_ret

    # 2) Get vertical barrier(max holding period)
    if vertical_barrier_times is False:
        vertical_barrier_times = pd.Series(pd.NaT, index=t_events)

    # 3) From events object, apply stop loss on vertical barrier
    if side_prediction is None:
        side_ = pd.Series(1., index=target.index)
        pt_sl_ = [pt_sl[0], pt_sl[0]]
    else:
        side_ = side_prediction.loc[target.index]
        pt_sl_ = pt_sl[:2]

    # Create a new df with [v_barrier, target, side] and drop rows that are NA in target
    events = pd.concat({'t1': vertical_barrier_times, 'trgt': target, 'side': side_}, axis=1)
    events = events.dropna(subset=['trgt'])

    # Apply Triple Barrier
    df0 = mp_pandas_obj(func=apply_pt_sl_on_t1,
                        pd_obj=('molecule', events.index),
                        num_threads=num_threads,
                        close=close,
                        events=events,
                        pt_sl=pt_sl_)

    events['t1'] = df0.dropna(how='all').min(axis=1)    # pd.min ignores nan

    if side_prediction is None:
        events = events.drop('side', axis=1)

    return events


# Snippet 3.9, pg 55, Question 3.3
def barrier_touched(out_df):
    """
    Snippet 3.9, pg 55, Question 3.3
    Adjust the getBins function (Snippet 3.7) to return a 0 whenever the vertical barrier is the one touched first.

    Top horizontal barrier: 1
    Bottom horizontal barrier: -1
    Vertical barrier: 0

    :param out_df:
    :return:
    """
    store = []
    for i in np.arange(len(out_df)):
        date_time = out_df.index[i]
        ret = out_df.loc[date_time, 'ret']
        target = out_df.loc[date_time, 'trgt']

        if ret > 0.0 and ret > target:
            # Top barrier reached
            store.append(1)
        elif ret < 0.0 and ret < -target:
            # Bottom barrier reached
            store.append(-1)
        else:
            # Vertical barrier reached
            store.append(0)

    out_df['bin'] = store
    return out_df


# Snippet 3.4 -> 3.7, page 51, Labeling for Side & Size with Meta Labels
def get_bins(triple_barrier_events, close):
    """
    Snippet 3.7, page 51, Labeling for Side & Size with Meta Labels

    Compute event's outcome (including side information, if provided).
    events is a DataFrame where:

    Now the possible values for labels in out['bin'] are {0,1}, as opposed to whether to take the bet or pass,
    a purely binary prediction. When the predicted label the previous feasible values {-1,0,1}.
    The ML algorithm will be trained to decide is 1, we can use the probability of this secondary prediction
    to derive the size of the bet, where the side (sign) of the position has been set by the primary model.

    :param triple_barrier_events: (data frame)
        - events.index is event's starttime
        - events['t1'] is event's endtime
        - events['trgt'] is event's target
        - events['side'] (optional) implies the algo's position side
        Case 1: ('side' not in events): bin in (-1,1) <- label by price action
        Case 2: ('side' in events): bin in (0,1) <- label by pnl (meta-labeling)
    :param close: (series) close prices
    :return: (data frame) of meta-labeled events
    """

    # 1) Align prices with their respective events
    events_ = triple_barrier_events.dropna(subset=['t1'])
    prices = events_.index.union(events_['t1'].values)
    prices = prices.drop_duplicates()
    # prices = close.reindex(prices, method='bfill')
    prices = close.reindex(prices).bfill()

    # 2) Create out DataFrame
    out_df = pd.DataFrame(index=events_.index)

    # Need to take the log returns, else your results will be skewed for short positions
    out_df['ret'] = np.log(prices.loc[events_['t1'].values].values) - np.log(prices.loc[events_.index])
    out_df['trgt'] = events_['trgt']

    # Meta labeling: Events that were correct will have pos returns
    if 'side' in events_:
        out_df['ret'] = out_df['ret'] * events_['side']     # meta-labeling

    # Added code: label 0 when vertical barrier reached
    out_df = barrier_touched(out_df)

    # Meta labeling: label incorrect events with a 0
    if 'side' in events_:
        out_df.loc[out_df['ret'] <= 0, 'bin'] = 0

    # Transform the log returns back to normal returns.
    out_df['ret'] = np.exp(out_df['ret']) - 1

    # Add the side to the output. This is useful for when a meta label model must be fit
    tb_cols = triple_barrier_events.columns
    if 'side' in tb_cols:
        out_df['side'] = triple_barrier_events['side']

    return out_df
